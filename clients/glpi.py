"""GLPI REST client (target system).

Session lifecycle (spec section 2):
    GET /initSession   Authorization: user_token <token> + App-Token
                       -> {"session_token": "..."}
    every call         Session-Token + App-Token
    GET /killSession

Use the client as a context manager so killSession always runs, including on
exceptions.
"""

from __future__ import annotations

from typing import Any

import requests

from clients.errors import (
    GlpiError,
    GlpiItemtypeNotFoundError,
    GlpiRightMissingError,
)
from config.settings import (
    CONTAINER_ID_ADDITIONAL_FIELDS,
    CONTAINER_ID_FATURAMENTO,
    DROPDOWN_FETCH_RANGE,
    HTTP_TIMEOUT_SECONDS,
    ITEMTYPE_ADDITIONAL_FIELDS,
    ITEMTYPE_FATURAMENTO,
)
from report import messages

OK_STATUS_CODES = frozenset({200, 201, 206, 207})


def _error_code(payload: Any) -> str | None:
    """GLPI reports failures as ["ERROR_CODE", "human message"]."""
    if isinstance(payload, list) and payload and isinstance(payload[0], str):
        if payload[0].startswith("ERROR"):
            return payload[0]
    return None


class GlpiClient:
    def __init__(
        self,
        base_url: str,
        user_token: str,
        app_token: str,
        timeout: int = HTTP_TIMEOUT_SECONDS,
    ):
        self._base_url = base_url.rstrip("/")
        self._user_token = user_token
        self._app_token = app_token
        self._timeout = timeout
        self._session_token: str | None = None
        self._http = requests.Session()
        self._http.headers.update({"Content-Type": "application/json"})
        # Dropdown dictionaries cached at preflight: itemtype -> {lookup_key: id}
        self._dropdown_cache: dict[str, dict[str, int]] = {}

    # -- session -----------------------------------------------------------

    def init_session(self) -> None:
        payload = self._request(
            "GET",
            "/initSession",
            headers={
                "Authorization": f"user_token {self._user_token}",
                "App-Token": self._app_token,
            },
            authenticated=False,
        )
        token = payload.get("session_token") if isinstance(payload, dict) else None
        if not token:
            raise GlpiError(messages.redact(f"initSession: resposta inesperada ({payload})"))
        self._session_token = token
        # The session token is a live credential too - never let it be echoed.
        messages.register_secrets([token])

    def kill_session(self) -> None:
        if not self._session_token:
            return
        try:
            self._request("GET", "/killSession")
        except GlpiError:
            # Closing the session is best-effort; a failure here must not mask
            # the real error that triggered the exit.
            pass
        finally:
            self._session_token = None

    def __enter__(self) -> "GlpiClient":
        self.init_session()
        return self

    def __exit__(self, *exc_info) -> None:
        self.kill_session()
        self._http.close()

    # -- low level ---------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        if not self._session_token:
            raise GlpiError("Sessão GLPI não iniciada.")
        return {
            "Session-Token": self._session_token,
            "App-Token": self._app_token,
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        headers: dict | None = None,
        authenticated: bool = True,
    ) -> Any:
        url = f"{self._base_url}{path}"
        request_headers = dict(headers or {})
        if authenticated:
            request_headers.update(self._auth_headers())

        try:
            response = self._http.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=request_headers,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise GlpiError(
                messages.redact(
                    messages.CONNECTION_ERROR.format(system="GLPI", detail=exc)
                )
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None

        code = _error_code(payload)
        if code == "ERROR_RIGHT_MISSING":
            raise GlpiRightMissingError(messages.redact(str(payload)))
        if code == "ERROR_ITEMTYPE_NOT_FOUND":
            raise GlpiItemtypeNotFoundError(messages.redact(str(payload)))

        if response.status_code not in OK_STATUS_CODES:
            detail = payload if payload is not None else response.text[:500]
            raise GlpiError(
                messages.redact(
                    messages.HTTP_ERROR.format(
                        status=response.status_code,
                        method=method,
                        path=path,
                        detail=detail,
                    )
                )
            )

        return payload

    # -- preflight ---------------------------------------------------------

    def check_fields_plugin_rights(self) -> None:
        """Spec 9.0 step 2. Raises GlpiRightMissingError when the profile lacks
        read/write access to the Fields plugin."""
        self._request("GET", "/PluginFieldsField", params={"range": "0-0"})

    # -- dropdown dictionaries (spec 6.3) ----------------------------------

    @staticmethod
    def _lookup_key(value: str) -> str:
        """Normalise for comparison: strip + case-insensitive, per spec 6.3."""
        return " ".join(str(value).split()).casefold()

    def load_dropdown(self, itemtype: str) -> dict[str, int]:
        """Fetch a whole dictionary in one call and cache it by normalised name.

        The GLPI API reported these itemtypes in two capitalisations
        ('...fielddropdown' in `links`, '...fieldDropdown' in
        `listSearchOptions`). We use the lowercase form and fall back to the
        capitalised one on ERROR_ITEMTYPE_NOT_FOUND (spec 6.3, DO WERYFIKACJI).
        """
        if itemtype in self._dropdown_cache:
            return self._dropdown_cache[itemtype]

        try:
            rows = self._request(
                "GET", f"/{itemtype}", params={"range": DROPDOWN_FETCH_RANGE}
            )
        except GlpiItemtypeNotFoundError:
            alternative = self._capitalised_dropdown_itemtype(itemtype)
            if alternative == itemtype:
                raise
            rows = self._request(
                "GET", f"/{alternative}", params={"range": DROPDOWN_FETCH_RANGE}
            )

        entries: dict[str, int] = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if row_id is None:
                continue
            for key in ("name", "completename"):
                value = row.get(key)
                if value:
                    entries.setdefault(self._lookup_key(value), int(row_id))

        self._dropdown_cache[itemtype] = entries
        return entries

    @staticmethod
    def _capitalised_dropdown_itemtype(itemtype: str) -> str:
        suffix = "fielddropdown"
        if itemtype.endswith(suffix):
            return itemtype[: -len(suffix)] + "fieldDropdown"
        return itemtype

    def resolve_dropdown(self, itemtype: str, value: str) -> int | None:
        """Name -> id. Returns None when there is no match.

        Policy (closed decision, spec 6.3): no match means skip the field and
        warn. NEVER create a new dictionary entry automatically.
        """
        if not value:
            return None
        return self.load_dropdown(itemtype).get(self._lookup_key(value))

    def dropdown_cache_size(self, itemtype: str) -> int:
        return len(self._dropdown_cache.get(itemtype, {}))

    # -- core objects ------------------------------------------------------

    def create_project(self, payload: dict) -> int:
        return self._create("/Project", payload)

    def create_project_task(self, payload: dict) -> int:
        return self._create("/ProjectTask", payload)

    def _create(self, path: str, payload: dict) -> int:
        response = self._request("POST", path, json_body={"input": payload})
        new_id = self._extract_id(response)
        if new_id is None:
            raise GlpiError(
                messages.redact(f"POST {path}: resposta sem id ({response})")
            )
        return new_id

    @staticmethod
    def _extract_id(response: Any) -> int | None:
        if isinstance(response, dict) and response.get("id") is not None:
            return int(response["id"])
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict) and item.get("id") is not None:
                    return int(item["id"])
        return None

    # -- plugin container rows ---------------------------------------------

    def get_container_rows(self, itemtype: str, items_id: int) -> list[dict]:
        """Rows of a plugin container already attached to a GLPI item.

        searchText in GLPI is a LIKE '%value%' match, so results are filtered
        for an exact items_id here.
        """
        rows = self._search(itemtype, {"searchText[items_id]": str(int(items_id))})
        return [row for row in rows if str(row.get("items_id")) == str(int(items_id))]

    def find_by_rdmfield(self, issue_id: int) -> list[dict]:
        """Deduplication lookup (spec 9.1).

        Exact-match filtering matters: searchText is a substring match, so
        rdmfield=2023 would otherwise also match the migrated issue 20238.
        """
        wanted = str(int(issue_id))
        rows = self._search(ITEMTYPE_ADDITIONAL_FIELDS, {"searchText[rdmfield]": wanted})
        return [row for row in rows if str(row.get("rdmfield", "")).strip() == wanted]

    def _search(self, itemtype: str, params: dict) -> list[dict]:
        try:
            payload = self._request("GET", f"/{itemtype}", params=params)
        except GlpiError as exc:
            # GLPI answers 400/404 for "no results" in some versions; that is
            # an empty result set, not a failure.
            if "ERROR_GLPI_SEARCH" in str(exc) or "404" in str(exc):
                return []
            raise
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        return []

    def create_container_row(self, itemtype: str, payload: dict) -> int:
        return self._create(f"/{itemtype}", payload)

    def update_container_row(self, itemtype: str, row_id: int, payload: dict) -> None:
        body = dict(payload)
        body["id"] = int(row_id)
        self._request("PUT", f"/{itemtype}/{int(row_id)}", json_body={"input": body})

    def write_additional_fields_row(self, project_id: int, values: dict) -> int:
        """Container 15 - one row per project (spec 6.1).

        Mandatory sequence: after POST /Project, check whether the plugin
        already created the row itself; update it if so, insert it if not. Both
        branches are required - the plugin behaves differently across versions.
        """
        payload = dict(values)
        payload.update(
            {
                "items_id": int(project_id),
                "itemtype": "Project",
                "plugin_fields_containers_id": CONTAINER_ID_ADDITIONAL_FIELDS,
            }
        )
        existing = self.get_container_rows(ITEMTYPE_ADDITIONAL_FIELDS, project_id)
        if existing:
            row_id = int(existing[0]["id"])
            self.update_container_row(ITEMTYPE_ADDITIONAL_FIELDS, row_id, payload)
            return row_id
        return self.create_container_row(ITEMTYPE_ADDITIONAL_FIELDS, payload)

    def create_faturamento_row(self, project_id: int, values: dict) -> int:
        """Container 25 - "tab" type, so several rows per project are allowed.

        Rows are attached to Project only, never to ProjectTask. The twin
        container 26 is not used by this migration.
        """
        payload = dict(values)
        payload.update(
            {
                "items_id": int(project_id),
                "itemtype": "Project",
                "plugin_fields_containers_id": CONTAINER_ID_FATURAMENTO,
            }
        )
        return self.create_container_row(ITEMTYPE_FATURAMENTO, payload)

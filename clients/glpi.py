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

import json
from pathlib import Path
from typing import Any

import requests

from clients.errors import (
    ApiError,
    GlpiError,
    GlpiItemtypeNotFoundError,
    GlpiRightMissingError,
)
from config.settings import (
    CONTAINER_ID_ADDITIONAL_FIELDS,
    CONTAINER_ID_FATURAMENTO,
    DOCUMENT_MARKER_PREFIX,
    DOCUMENT_TYPE_FETCH_RANGE,
    DROPDOWN_FETCH_RANGE,
    GLPI_RIGHT_CREATE,
    GLPI_RIGHT_UPDATE,
    GLPI_RIGHTNAME_DOCUMENT,
    GLPI_RIGHTNAME_PROJECTTASK,
    HTTP_TIMEOUT_SECONDS,
    ITEMTYPE_ADDITIONAL_FIELDS,
    ITEMTYPE_DOCUMENT,
    ITEMTYPE_DOCUMENT_ITEM,
    ITEMTYPE_FATURAMENTO,
    SEARCH_FETCH_RANGE,
)
from report import messages

OK_STATUS_CODES = frozenset({200, 201, 206, 207})


def _has_exact_marker(comment: Any, marker: str) -> bool:
    """True when `marker` is a whole line of `comment`.

    Line-anchored on purpose: the marker is written as the first line of the
    comment, so `rdmattachment:2931` cannot match `rdmattachment:29314`.
    """
    if not comment:
        return False
    return any(line.strip() == marker for line in str(comment).splitlines())


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
        # glpi_documenttypes extensions, loaded once at preflight. None means
        # "not loaded", which the report treats as "cannot warn", never as
        # "no extension is allowed".
        self._document_types: set[str] | None = None

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

    def can_write_projecttask_containers(self) -> bool | None:
        """Whether the API profile may insert a container row on a ProjectTask.

        `GET /PluginFieldsField` above only proves READ access, which is why the
        container-26 failure of 2026-08-07 slipped past preflight: the plugin
        checks the HOST itemtype's plain UPDATE bit when inserting the row, and
        the profile had it for `project` but not for `projecttask`. See the
        GLPI_RIGHT_UPDATE block in config/settings.py for the full diagnosis.

        Read-only - the right cannot be probed with a real write during a
        dry-run. Returns None when the answer is unknown (endpoint unavailable,
        key absent, non-numeric value): an unknown is never reported as a
        missing right, in keeping with resolve/ returning None on a miss rather
        than inventing an answer.
        """
        try:
            payload = self._request("GET", "/getActiveProfile")
        except ApiError:
            return None

        profile = payload.get("active_profile", payload) if isinstance(payload, dict) else None
        if not isinstance(profile, dict):
            return None

        try:
            right = int(profile[GLPI_RIGHTNAME_PROJECTTASK])
        except (KeyError, TypeError, ValueError):
            return None
        return bool(right & GLPI_RIGHT_UPDATE)

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

    def _search(self, itemtype: str, params: dict, full_range: bool = False) -> list[dict]:
        """GLPI list search. `full_range` lifts the server's default page size.

        TRAP, measured 2026-08-10 on project 1277: without an explicit `range`
        GLPI returns only the first **15** rows. The project had 19 documents
        and the read-back reported 15, which would make _ensure_link believe the
        last four were unlinked and post duplicates GLPI then refuses - a
        failure that looks like a rights problem and is not one.
        """
        if full_range:
            params = {**params, "range": SEARCH_FETCH_RANGE}
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

    def get_item(self, itemtype: str, item_id: int) -> dict | None:
        """One item, or None when it no longer exists.

        Used by reset_migration.py to tell an orphaned `rdmfield` marker from a
        live one: a container row survives the project it was attached to, so
        "the marker exists" does not mean "the project exists". A deleted item
        answers 404 or ERROR_ITEM_NOT_FOUND - both mean absent, not broken.
        Note a project in the GLPI trash still answers here, with is_deleted 1.
        """
        try:
            payload = self._request("GET", f"/{itemtype}/{int(item_id)}")
        except GlpiError as exc:
            if "ERROR_ITEM_NOT_FOUND" in str(exc) or "404" in str(exc):
                return None
            raise
        return payload if isinstance(payload, dict) else None

    def delete_item(self, itemtype: str, item_id: int, force_purge: bool = True) -> None:
        """Delete one item. The only destructive call in this client.

        Written for reset_migration.py and called there on plugin container rows
        only - never on a Project or a ProjectTask. force_purge skips the trash,
        which is what an orphaned container row needs: left in the bin its
        `rdmfield` value would still be found by find_by_rdmfield and keep
        blocking the migration.
        """
        body: dict[str, Any] = {"input": {"id": int(item_id)}}
        if force_purge:
            body["force_purge"] = True
        self._request("DELETE", f"/{itemtype}/{int(item_id)}", json_body=body)

    # -- documents (attachments) -------------------------------------------

    def can_create_documents(self) -> bool | None:
        """Whether the API profile may create a Document.

        Same shape and same caution as can_write_projecttask_containers: None
        means "could not tell", and an unknown is never reported as a missing
        right. Read live 2026-08-10 the profile answered document = 255.
        """
        try:
            payload = self._request("GET", "/getActiveProfile")
        except ApiError:
            return None

        profile = payload.get("active_profile", payload) if isinstance(payload, dict) else None
        if not isinstance(profile, dict):
            return None

        try:
            right = int(profile[GLPI_RIGHTNAME_DOCUMENT])
        except (KeyError, TypeError, ValueError):
            return None
        return bool(right & GLPI_RIGHT_CREATE)

    def load_document_types(self) -> set[str]:
        """Extensions GLPI accepts, from glpi_documenttypes. Cached.

        Used for a REPORT WARNING only. GLPI stores some `ext` values as
        patterns rather than plain strings, so a miss here can be a false alarm
        - the upload is attempted anyway and GLPI stays the authority. Verified
        2026-08-10: 76 rows, with .xlsb/.msg/.eml/.ods present and uploadable,
        which are exactly the ones RDM 16467 needs.
        """
        if self._document_types is not None:
            return self._document_types

        rows = self._request(
            "GET", "/DocumentType", params={"range": DOCUMENT_TYPE_FETCH_RANGE}
        )
        extensions: set[str] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            # is_uploadable 0 means the type exists but the upload form refuses
            # it, which for our purposes is the same as absent.
            if not int(row.get("is_uploadable") or 0):
                continue
            ext = str(row.get("ext") or "").strip().lower()
            if ext:
                extensions.add(ext)
        self._document_types = extensions
        return extensions

    def document_type_extensions(self) -> set[str] | None:
        """The cached extension set, or None when preflight could not load it."""
        return self._document_types

    def find_document_by_marker(self, attachment_id: int) -> list[dict]:
        """Documents carrying the migration marker for one Redmine attachment.

        The document twin of find_by_rdmfield, and it needs the same defence:
        searchText is a LIKE '%value%' match, so `rdmattachment:2931` would also
        match `rdmattachment:29314`. Results are filtered for an exact marker,
        delimited by the line break the comment is built with.
        """
        marker = f"{DOCUMENT_MARKER_PREFIX}{int(attachment_id)}"
        rows = self._search(ITEMTYPE_DOCUMENT, {"searchText[comment]": marker})
        return [row for row in rows if _has_exact_marker(row.get("comment"), marker)]

    def upload_document(self, path, name: str, comment: str) -> int:
        """POST /Document with the file itself. Returns the new document id.

        The legacy API takes an upload as multipart/form-data with a JSON
        manifest beside the bytes:

            uploadManifest = {"input": {"name": ..., "_filename": ["file.pdf"]}}
            filename[0]    = <the bytes>

        `_filename` must repeat the name of the uploaded part - GLPI matches the
        two to find the file.

        TRAP: this client sets Content-Type: application/json on the session
        (see __init__). requests only computes the multipart boundary when the
        header is absent, so it has to be cleared for THIS request - otherwise
        GLPI receives a body it cannot parse and reports an empty upload. That
        is why uploads do not go through _request().

        entities_id is deliberately not sent, for the reason recorded in
        config/settings.py: GLPI files the item in the session's active entity.
        """
        file_path = Path(path)
        manifest = {
            "input": {
                "name": name,
                "comment": comment,
                "_filename": [file_path.name],
            }
        }

        with file_path.open("rb") as handle:
            files = {
                "uploadManifest": (
                    None,
                    json.dumps(manifest, ensure_ascii=False),
                    "application/json",
                ),
                "filename[0]": (file_path.name, handle, "application/octet-stream"),
            }
            payload = self._upload("/Document", files)

        new_id = self._extract_id(payload)
        if new_id is None:
            raise GlpiError(
                messages.redact(f"POST /Document: resposta sem id ({payload})")
            )
        return new_id

    def link_document(self, document_id: int, itemtype: str, items_id: int) -> int:
        """Attach an existing Document to an item (the "Documentos" tab).

        Done as an explicit POST /Document_Item rather than by passing
        itemtype/items_id in the upload input: the result is deterministic, and
        the same call re-links a document that already exists in GLPI - the
        deduplication path, where there is nothing to upload.
        """
        return self._create(
            f"/{ITEMTYPE_DOCUMENT_ITEM}",
            {
                "documents_id": int(document_id),
                "itemtype": itemtype,
                "items_id": int(items_id),
            },
        )

    def document_links(self, itemtype: str, items_id: int) -> list[dict]:
        """Document_Item rows already attached to one item.

        searchText is a substring match on both fields here, so the rows are
        filtered for exact equality - `items_id=1` would otherwise match 1265.
        """
        rows = self._search(
            ITEMTYPE_DOCUMENT_ITEM,
            {
                "searchText[itemtype]": itemtype,
                "searchText[items_id]": str(int(items_id)),
            },
            full_range=True,
        )
        return [
            row
            for row in rows
            if str(row.get("items_id")) == str(int(items_id))
            and str(row.get("itemtype")) == itemtype
        ]

    def _upload(self, path: str, files: dict) -> Any:
        """Multipart POST. See upload_document for why this bypasses _request."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers()
        # None removes the session-level application/json so requests can set
        # multipart/form-data with its own boundary.
        headers["Content-Type"] = None

        try:
            response = self._http.post(
                url, files=files, headers=headers, timeout=self._timeout
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

        if _error_code(payload) == "ERROR_RIGHT_MISSING":
            raise GlpiRightMissingError(messages.redact(str(payload)))

        if response.status_code not in OK_STATUS_CODES:
            detail = payload if payload is not None else response.text[:500]
            raise GlpiError(
                messages.redact(
                    messages.HTTP_ERROR.format(
                        status=response.status_code,
                        method="POST",
                        path=path,
                        detail=detail,
                    )
                )
            )
        return payload

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

    def create_faturamento_row(self, task_id: int, values: dict) -> int:
        """Container 26 - "tab" type, attached to a ProjectTask.

        CHANGED 2026-08-06: rows used to hang off the Project (container 25).
        They now hang off the Faturamento task, one row per task.

        A "tab" container row is written straight to the container itemtype,
        which never reaches the plugin's validateValues() - verified in plugin
        source 1.24.3. Container 26's five mandatory flags therefore do not
        refuse this write; a missing value only leaves the row incomplete, and
        the dry-run report says so.
        """
        payload = dict(values)
        payload.update(
            {
                "items_id": int(task_id),
                "itemtype": "ProjectTask",
                "plugin_fields_containers_id": CONTAINER_ID_FATURAMENTO,
            }
        )
        return self.create_container_row(ITEMTYPE_FATURAMENTO, payload)

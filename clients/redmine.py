"""Redmine REST client (source system).

Authentication: X-Redmine-API-Key header (spec section 2).

Redmine and GLPI are two DIFFERENT servers. 'apirest.php' belongs to GLPI only
and must never be appended to REDMINE_URL - config.settings.load_settings()
rejects that mistake at startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import requests

from clients.errors import RedmineError
from config.settings import HTTP_TIMEOUT_SECONDS
from report import messages

DEFAULT_INCLUDE = ("children", "attachments", "relations")


@dataclass
class TreeNode:
    """One Redmine issue plus its fully fetched descendants."""

    issue: dict
    children: list["TreeNode"] = field(default_factory=list)

    @property
    def issue_id(self) -> int:
        return int(self.issue["id"])

    @property
    def tracker_id(self) -> int | None:
        tracker = self.issue.get("tracker") or {}
        return tracker.get("id")

    @property
    def subject(self) -> str:
        return self.issue.get("subject") or ""

    def walk(self):
        """Yield (node, depth) pre-order: parent before child (spec 9.2)."""
        stack = [(self, 0)]
        while stack:
            node, depth = stack.pop()
            yield node, depth
            for child in reversed(node.children):
                stack.append((child, depth + 1))


@dataclass
class TreeFetchResult:
    root: TreeNode
    # Children we could not retrieve. They must still reach the report -
    # nothing may disappear silently.
    failures: list[tuple[int, str]] = field(default_factory=list)
    # Issue ids seen twice; kept so a cyclic tree is visible in the report.
    cycles: list[int] = field(default_factory=list)


class RedmineClient:
    """Thin wrapper over the Redmine issues API."""

    def __init__(self, base_url: str, api_key: str, timeout: int = HTTP_TIMEOUT_SECONDS):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Redmine-API-Key": api_key,
                "Accept": "application/json",
            }
        )

    # -- low level ---------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise RedmineError(
                messages.redact(
                    messages.CONNECTION_ERROR.format(system="Redmine", detail=exc)
                )
            ) from exc

        if response.status_code >= 400:
            raise RedmineError(
                messages.redact(
                    messages.HTTP_ERROR.format(
                        status=response.status_code,
                        method="GET",
                        path=path,
                        detail=response.text[:500],
                    )
                )
            )

        try:
            return response.json()
        except ValueError as exc:
            raise RedmineError(
                messages.redact(
                    messages.HTTP_ERROR.format(
                        status=response.status_code,
                        method="GET",
                        path=path,
                        detail="resposta não é JSON válido",
                    )
                )
            ) from exc

    # -- public API --------------------------------------------------------

    def fetch_issue(self, issue_id: int, include: Iterable[str] = DEFAULT_INCLUDE) -> dict:
        """GET /issues/{id}.json?include=...

        Returns the `issue` object itself, not the envelope.
        """
        params = {"include": ",".join(include)} if include else None
        payload = self._get(f"/issues/{int(issue_id)}.json", params=params)
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            raise RedmineError(
                messages.REDMINE_ISSUE_NOT_FOUND.format(issue_id=issue_id)
            )
        return issue

    def iter_issues(self, tracker_id: int, page_size: int = 100):
        """Yield every issue of a tracker, closed ones included.

        status_id=* is required - without it Redmine returns open issues only,
        which would understate the real set of dropdown values in use.
        """
        offset = 0
        while True:
            payload = self._get(
                "/issues.json",
                params={
                    "tracker_id": int(tracker_id),
                    "status_id": "*",
                    "limit": page_size,
                    "offset": offset,
                },
            )
            issues = payload.get("issues") or []
            for issue in issues:
                yield issue
            offset += len(issues)
            if not issues or offset >= int(payload.get("total_count", 0)):
                break

    def fetch_tree(self, root_id: int) -> TreeFetchResult:
        """Fetch the root issue and every descendant, recursively.

        Two verified traps drive this implementation (spec section 3):

        1. `include=children` returns only id/tracker/subject for children -
           no dates, no custom fields. Each child therefore needs its own GET.
        2. The `children` key may be absent entirely (issue 17582 has none while
           19074 has four), so we always use .get("children", []).

        A `visited` set guards against cycles.
        """
        result = TreeFetchResult(root=TreeNode(issue=self.fetch_issue(root_id)))
        visited: set[int] = {int(root_id)}
        self._expand(result.root, visited, result)
        return result

    def _expand(self, node: TreeNode, visited: set[int], result: TreeFetchResult) -> None:
        for stub in node.issue.get("children", []) or []:
            child_id = stub.get("id")
            if child_id is None:
                continue
            child_id = int(child_id)

            if child_id in visited:
                result.cycles.append(child_id)
                continue
            visited.add(child_id)

            try:
                # include=children only; relations of descendants are not part
                # of the Faturamento algorithm (spec 6.5 uses the root's).
                child_issue = self.fetch_issue(child_id, include=("children",))
            except RedmineError as exc:
                result.failures.append((child_id, str(exc)))
                continue

            child_node = TreeNode(issue=child_issue)
            node.children.append(child_node)
            self._expand(child_node, visited, result)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RedmineClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def relation_partner_id(relation: dict, current_issue_id: int) -> int | None:
    """Return the id of the issue on the other side of a relation.

    TRAP (spec 6.5): Redmine stores a relation once, in whichever direction it
    was created. Issue 17582 holds its own id in `issue_id`, while issue 20389
    holds it in `issue_to_id`. Reading `issue_to_id` unconditionally would miss
    half of the links - so the partner is always "the field that is NOT us".
    """
    current = int(current_issue_id)
    for key in ("issue_id", "issue_to_id"):
        value = relation.get(key)
        if value is None:
            continue
        if int(value) != current:
            return int(value)
    return None

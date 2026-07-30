"""Redmine issue.assigned_to.id -> GLPI users_id (spec section 8).

Closed policy: a user with no GLPI counterpart - including powerbi/162, whose
entry exists with glpi_id: null - and an issue with no assigned_to at all leave
the field empty and add a warning. Never abort the migration for this reason.
"""

from __future__ import annotations

from config.settings import load_yaml


class UserResolver:
    def __init__(self, table: dict[int, dict] | None = None):
        if table is None:
            table = load_yaml("user_map.yml").get("users") or {}
        self._table = {int(key): value for key, value in table.items()}

    def resolve(self, redmine_user_id) -> int | None:
        """Return the GLPI user id, or None when unmapped or deliberately null."""
        if redmine_user_id is None:
            return None
        entry = self._table.get(int(redmine_user_id))
        if not entry:
            return None
        glpi_id = entry.get("glpi_id")
        return int(glpi_id) if glpi_id is not None else None

    def is_known(self, redmine_user_id) -> bool:
        """True when the user is in the map, even with an explicit null id.

        Distinguishes "we know this user has no GLPI account" (powerbi) from
        "we have never seen this user" - the two warrant different report lines.
        """
        if redmine_user_id is None:
            return False
        return int(redmine_user_id) in self._table

    def login(self, redmine_user_id) -> str:
        if redmine_user_id is None:
            return ""
        entry = self._table.get(int(redmine_user_id)) or {}
        return entry.get("login") or ""

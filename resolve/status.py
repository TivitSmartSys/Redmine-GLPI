"""Redmine issue.status.id -> GLPI projectstates_id (spec section 7).

The same map serves Project and ProjectTask. A status outside the map means:
skip the field, add a warning, never abort the migration.
"""

from __future__ import annotations

from config.settings import load_yaml


class StatusResolver:
    def __init__(self, table: dict[int, dict] | None = None):
        if table is None:
            table = load_yaml("status_map.yml").get("statuses") or {}
        self._table = {int(key): value for key, value in table.items()}

    def resolve(self, redmine_status_id) -> int | None:
        """Return the GLPI project state id, or None when unmapped."""
        if redmine_status_id is None:
            return None
        entry = self._table.get(int(redmine_status_id))
        if not entry:
            return None
        glpi_id = entry.get("glpi_id")
        return int(glpi_id) if glpi_id is not None else None

    def label(self, redmine_status_id) -> str:
        """Informational Redmine status name, for the report only."""
        if redmine_status_id is None:
            return ""
        entry = self._table.get(int(redmine_status_id)) or {}
        return entry.get("label") or ""

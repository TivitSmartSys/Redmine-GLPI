"""Dropdown value -> GLPI dictionary id (spec section 6.3).

Dictionaries are pulled once each at preflight and cached inside GlpiClient.
Comparison is case-insensitive after .strip() - required, not cosmetic: GLPI
stores Complexidade uppercase ("MÉDIA") while Redmine sends title case ("Média").

Closed policy: no match means skip the field and warn. NEVER create a dictionary
entry automatically.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DropdownMiss:
    """One value that exists in Redmine but not in the GLPI dictionary."""

    itemtype: str
    field_name: str
    value: str


class DropdownResolver:
    def __init__(self, glpi_client):
        self._glpi = glpi_client
        self.misses: list[DropdownMiss] = []

    def resolve(self, itemtype: str, field_name: str, value: str) -> int | None:
        resolved = self._glpi.resolve_dropdown(itemtype, value)
        if resolved is None:
            self.misses.append(
                DropdownMiss(itemtype=itemtype, field_name=field_name, value=value)
            )
        return resolved

    def dictionary_is_empty(self, itemtype: str) -> bool:
        return not self._glpi.load_dropdown(itemtype)

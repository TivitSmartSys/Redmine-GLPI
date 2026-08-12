"""Redmine `Cliente` -> GLPI entity id.

Opened 2026-08-12. Until then `entities_id` was never sent and every project
landed in the API session's own entity (75). The map itself lives in
config/entity_map.yml; this module only turns a client name into an id.

Same contract as resolve/dropdowns.py, deliberately: a lookup that fails returns
None and is reported, never guessed and never auto-created (rule 2 of
CLAUDE.md). The difference is that a missing entity is not the end of the story -
the caller still creates the project, in DEFAULT_ENTITY_ID.

Resolution goes name -> name -> id, in two steps:

    "ENEL SP"  ->  "TIVIT > GRUPO ENEL > BRASIL > ENEL SP"  ->  4
       (entity_map.yml)                              (GET /Entity, live)

The second step is live on purpose. The spreadsheet's ids belong to the TEST
instance, so hard-coding them would file projects into unrelated entities the
moment this runs anywhere else. `id_teste` survives only as a cross-check.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import DEFAULT_ENTITY_ID, ENTITY_MAP_FILENAME, load_yaml


def lookup_key(value: str) -> str:
    """Normalise a client name for comparison: strip + collapse + casefold.

    Matches GlpiClient._lookup_key. Required, not cosmetic: the sheet writes
    "ENEL RJ – Cabeamento" with an en dash while Redmine and GLPI both use a
    plain hyphen, and casing is inconsistent across all three.
    """
    return " ".join(str(value).split()).casefold()


# How a client name was classified. Kept as plain strings rather than
# transform.mapper.Outcome so resolve/ never imports transform/ - the mapper
# owns the translation into report outcomes.
STATUS_MAPPED = "mapped"                    # -> Outcome.WRITTEN
STATUS_EMPTY = "empty"                      # -> Outcome.EMPTY_SOURCE
STATUS_UNMAPPED = "unmapped"                # -> Outcome.NO_COUNTERPART
STATUS_NOT_MIGRATED = "nao_sera_migrado"    # -> Outcome.NO_COUNTERPART
STATUS_UNRESOLVED = "unresolved"            # -> Outcome.UNRESOLVED


@dataclass
class EntityDecision:
    """Where one project goes, and why."""

    entity_id: int
    status: str
    client: str = ""
    completename: str = ""

    @property
    def is_written(self) -> bool:
        return self.status == STATUS_MAPPED


@dataclass
class EntityMiss:
    """A client whose entity could not be used, with the reason."""

    client: str
    status: str
    completename: str = ""


@dataclass
class IdMismatch:
    """The sheet's id_teste disagrees with the live instance."""

    completename: str
    id_teste: int
    resolved: int | None


def load_entity_map() -> dict:
    return load_yaml(ENTITY_MAP_FILENAME)


class EntityResolver:
    def __init__(self, glpi_client, entity_map: dict | None = None):
        self._glpi = glpi_client
        raw = entity_map if entity_map is not None else load_entity_map()
        self._entries: list[dict] = list(raw.get("entities") or [])
        # client -> completename
        self._by_client: dict[str, str] = {}
        for entry in self._entries:
            completename = str(entry.get("completename") or "").strip()
            if not completename:
                continue
            for client in entry.get("clients") or []:
                self._by_client.setdefault(lookup_key(client), completename)
        self._not_migrated: set[str] = {
            lookup_key(name) for name in raw.get("nao_sera_migrado") or []
        }
        self.misses: list[EntityMiss] = []

    # -- resolution --------------------------------------------------------

    def decide(self, client: str) -> EntityDecision:
        """Classify one client value. Never raises, never creates an entity.

        Every branch other than STATUS_MAPPED falls back to DEFAULT_ENTITY_ID,
        so the project is always created - the entity is the only thing at
        stake, and losing it must not cost the migration.
        """
        value = str(client or "").strip()
        if not value:
            return EntityDecision(DEFAULT_ENTITY_ID, STATUS_EMPTY)

        key = lookup_key(value)

        if key in self._not_migrated:
            miss = EntityMiss(client=value, status=STATUS_NOT_MIGRATED)
            self.misses.append(miss)
            return EntityDecision(DEFAULT_ENTITY_ID, STATUS_NOT_MIGRATED, client=value)

        completename = self._by_client.get(key)
        if not completename:
            self.misses.append(EntityMiss(client=value, status=STATUS_UNMAPPED))
            return EntityDecision(DEFAULT_ENTITY_ID, STATUS_UNMAPPED, client=value)

        entity_id = self._glpi.resolve_entity(completename)
        if entity_id is None:
            self.misses.append(
                EntityMiss(
                    client=value, status=STATUS_UNRESOLVED, completename=completename
                )
            )
            return EntityDecision(
                DEFAULT_ENTITY_ID,
                STATUS_UNRESOLVED,
                client=value,
                completename=completename,
            )

        return EntityDecision(
            entity_id, STATUS_MAPPED, client=value, completename=completename
        )

    # -- preflight ---------------------------------------------------------

    def crosscheck_ids(self) -> list[IdMismatch]:
        """Compare every id_teste against the live tree.

        A mismatch is not an error - it is the signal "this instance is not the
        one the spreadsheet was written against", which is exactly the situation
        that would otherwise file projects into wrong entities in silence.
        """
        mismatches: list[IdMismatch] = []
        for entry in self._entries:
            completename = str(entry.get("completename") or "").strip()
            id_teste = entry.get("id_teste")
            if not completename or id_teste is None:
                continue
            resolved = self._glpi.resolve_entity(completename)
            if resolved != int(id_teste):
                mismatches.append(
                    IdMismatch(
                        completename=completename,
                        id_teste=int(id_teste),
                        resolved=resolved,
                    )
                )
        return mismatches

    # -- introspection, used by tests and the report -----------------------

    @property
    def client_count(self) -> int:
        return len(self._by_client) + len(self._not_migrated)

    @property
    def entity_count(self) -> int:
        return len(self._entries)

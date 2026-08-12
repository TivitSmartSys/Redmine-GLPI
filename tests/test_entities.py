"""The project goes to the entity of its `Cliente` (opened 2026-08-12).

Until then entities_id was never sent and every project landed in the API
session's own entity (75). What is pinned here:

  1. a mapped client puts its entity id in the payload, as Outcome.WRITTEN;
  2. every other path still produces a project - in DEFAULT_ENTITY_ID - and
     says why, so losing an entity never costs the migration;
  3. entities_id is present ALWAYS. Preflight moves the session to the root
     entity, so an omitted key would file the project in entity 0 rather than
     in 75, which is the one silent way this feature could go wrong;
  4. the section-7 arithmetic still closes with the entity record in it. This is
     why "Nao sera migrado" is NO_COUNTERPART and not NEVER_WRITE: never_write
     records live off all_records() and would leave the sum short of the total;
  5. matching is case-insensitive after .strip(), and the real spellings are
     the ones in config/entity_map.yml - Redmine writes "ENEL RJ - Cabeamento"
     with a plain hyphen while the source spreadsheet used an en dash.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DEFAULT_ENTITY_ID  # noqa: E402
from report.reporter import ProjectPlan, Reporter  # noqa: E402
from resolve.entities import EntityResolver, load_entity_map  # noqa: E402
from transform.mapper import Mapper, Outcome  # noqa: E402

MAPPING = {
    "project_core": [
        {"column": "name", "transform": "text",
         "sources": [{"from": "attribute", "name": "subject"}]},
    ],
    "container15": [],
}

ENTITY_MAP = {
    "entities": [
        {"completename": "TIVIT > GRUPO ENEL > BRASIL > ENEL SP",
         "id_teste": 4, "clients": ["ENEL SP"]},
        {"completename": "TIVIT > GRUPO ENEL > BRASIL > ENEL RJ",
         "id_teste": 3, "clients": ["ENEL RJ", "ENEL RJ - Cabeamento"]},
        {"completename": "TIVIT > NAO EXISTE AQUI",
         "id_teste": 999, "clients": ["FANTASMA"]},
    ],
    "nao_sera_migrado": ["OUTRO", "CIEN"],
}

LIVE_ENTITIES = {
    "tivit > grupo enel > brasil > enel sp": 4,
    "tivit > grupo enel > brasil > enel rj": 3,
}


class FakeGlpi:
    """Only what EntityResolver touches: completename -> id, or None."""

    def __init__(self, entities=None):
        self._entities = LIVE_ENTITIES if entities is None else entities

    def resolve_entity(self, completename: str):
        return self._entities.get(" ".join(str(completename).split()).casefold())


def issue_with(client, subject="ENEL SP | Projeto") -> dict:
    custom_fields = [] if client is None else [{"name": "Cliente", "value": client}]
    return {
        "id": 20438,
        "subject": subject,
        "tracker": {"id": 14, "name": "Projeto"},
        "custom_fields": custom_fields,
    }


def mapped(client, glpi=None):
    mapper = Mapper(
        mapping=MAPPING,
        status_resolver=None,
        user_resolver=None,
        dropdown_resolver=None,
        entity_resolver=EntityResolver(glpi or FakeGlpi(), entity_map=ENTITY_MAP),
    )
    return mapper.map_project(issue_with(client))


def entity_record(core):
    return next(r for r in core.records if r.target_column == "entities_id")


# -- 1. the mapped path -----------------------------------------------------


def test_mapped_client_lands_in_its_entity():
    core, _ = mapped("ENEL SP")
    assert core.payload["entities_id"] == 4

    record = entity_record(core)
    assert record.outcome is Outcome.WRITTEN
    assert record.raw_value == "ENEL SP"
    assert record.detail == "TIVIT > GRUPO ENEL > BRASIL > ENEL SP"


def test_several_clients_may_share_one_entity():
    """EGP BR takes four names; ENEL RJ takes two. The alias is not an alias
    table - it is the sheet's own many-to-one column."""
    core, _ = mapped("ENEL RJ - Cabeamento")
    assert core.payload["entities_id"] == 3


def test_matching_ignores_case_and_surrounding_space():
    core, _ = mapped("  enel   sp  ")
    assert core.payload["entities_id"] == 4
    assert entity_record(core).outcome is Outcome.WRITTEN


# -- 2. every fallback path -------------------------------------------------


def test_empty_client_falls_back_and_is_reported():
    core, _ = mapped("")
    assert core.payload["entities_id"] == DEFAULT_ENTITY_ID
    assert entity_record(core).outcome is Outcome.EMPTY_SOURCE


def test_missing_custom_field_is_treated_as_empty():
    mapper = Mapper(
        mapping=MAPPING, status_resolver=None, user_resolver=None,
        dropdown_resolver=None,
        entity_resolver=EntityResolver(FakeGlpi(), entity_map=ENTITY_MAP),
    )
    core, _ = mapper.map_project(issue_with(None))
    assert core.payload["entities_id"] == DEFAULT_ENTITY_ID
    assert entity_record(core).outcome is Outcome.EMPTY_SOURCE


def test_client_outside_the_map_falls_back():
    """COELCE (481 issues) and AMPLA (456) take this path on real data. They are
    the former names of ENEL CE and ENEL RJ and are deliberately NOT aliased -
    the migrator must not decide that two client names are the same company."""
    core, _ = mapped("COELCE")
    record = entity_record(core)
    assert core.payload["entities_id"] == DEFAULT_ENTITY_ID
    assert record.outcome is Outcome.NO_COUNTERPART
    assert "sem entidade no mapa" in record.detail


def test_nao_sera_migrado_is_no_counterpart_not_never_write():
    """NEVER_WRITE would drop out of all_records() and break the section-7 sum."""
    core, _ = mapped("OUTRO")
    record = entity_record(core)
    assert core.payload["entities_id"] == DEFAULT_ENTITY_ID
    assert record.outcome is Outcome.NO_COUNTERPART
    assert "não será migrado" in record.detail


def test_entity_missing_on_this_instance_is_unresolved():
    core, _ = mapped("FANTASMA")
    record = entity_record(core)
    assert core.payload["entities_id"] == DEFAULT_ENTITY_ID
    assert record.outcome is Outcome.UNRESOLVED
    assert record.detail  # says the map points at an entity GLPI does not have


# -- 3. the key is always sent ---------------------------------------------


def test_entities_id_is_always_in_the_payload():
    for client in ("ENEL SP", "", "COELCE", "OUTRO", "FANTASMA"):
        core, _ = mapped(client)
        assert "entities_id" in core.payload, client


def test_mapper_without_an_entity_resolver_is_unchanged():
    """The argument is optional so the 99 older tests keep constructing Mapper
    with four arguments."""
    mapper = Mapper(mapping=MAPPING, status_resolver=None, user_resolver=None,
                    dropdown_resolver=None)
    core, _ = mapper.map_project(issue_with("ENEL SP"))
    assert "entities_id" not in core.payload
    assert not [r for r in core.records if r.target_column == "entities_id"]


# -- 4. the report ----------------------------------------------------------


def plan_for(client) -> ProjectPlan:
    core, container = mapped(client)
    return ProjectPlan(issue=issue_with(client), core=core, container15=container)


# Three records, not two: `name` and the entity, plus the sweep's own record
# for the `Cliente` custom field. MAPPING here has no container-15 entry for
# Cliente, so the sweep correctly reports it as having no counterpart - and that
# is the point of the assertion. Two records for one source field is intended:
# the check counts records, not distinct field names.


def test_integrity_total_still_closes_with_the_entity_record():
    assert "2 + 0 + 1 + 0 = 3" in Reporter(plan_for("ENEL SP")).render()


def test_integrity_total_closes_for_every_fallback_too():
    assert "1 + 2 + 0 + 0 = 3" in Reporter(plan_for("")).render()
    assert "1 + 0 + 2 + 0 = 3" in Reporter(plan_for("COELCE")).render()
    assert "1 + 0 + 2 + 0 = 3" in Reporter(plan_for("OUTRO")).render()
    assert "1 + 0 + 1 + 1 = 3" in Reporter(plan_for("FANTASMA")).render()


def test_report_header_names_the_entity_and_its_client():
    text = Reporter(plan_for("ENEL SP")).render()
    assert "TIVIT > GRUPO ENEL > BRASIL > ENEL SP" in text
    assert "id 4" in text


def test_report_header_says_when_the_default_was_used():
    text = Reporter(plan_for("COELCE")).render()
    assert "PADRÃO" in text
    assert f"id {DEFAULT_ENTITY_ID}" in text


# -- 5. the shipped map itself ---------------------------------------------


def test_shipped_map_has_no_duplicate_client():
    """One client name may not point at two entities - the resolver would take
    whichever came first and the report would never mention the other."""
    raw = load_entity_map()
    seen: dict[str, str] = {}
    for entry in raw["entities"]:
        for client in entry["clients"]:
            key = " ".join(client.split()).casefold()
            assert key not in seen, f"{client} maps to two entities"
            seen[key] = entry["completename"]
    for client in raw["nao_sera_migrado"]:
        key = " ".join(client.split()).casefold()
        assert key not in seen, f"{client} is both mapped and 'nao sera migrado'"
        seen[key] = "nao_sera_migrado"


def test_shipped_map_covers_the_clients_the_sheet_defines():
    raw = load_entity_map()
    assert len(raw["entities"]) == 37
    mapped_clients = sum(len(e["clients"]) for e in raw["entities"])
    # 43 names from the sheet plus one spelling variant of CEMIG_ROSAL ENERGIA
    # that only the GLPI dropdown uses.
    assert mapped_clients == 44
    assert "TIVIT > GRUPO ENEL > BRASIL > ENEL SP" in [
        e["completename"] for e in raw["entities"]
    ]


def test_shipped_map_uses_the_hyphen_redmine_actually_writes():
    """The source PDF used an en dash. Redmine and GLPI both use a hyphen, and
    matching is exact after casefold - so the en dash would never match."""
    raw = load_entity_map()
    names = [c for e in raw["entities"] for c in e["clients"]]
    assert "ENEL RJ - Cabeamento" in names
    assert "EQUATORIAL GO - Automação" in names
    assert not [n for n in names if "–" in n]


def test_every_entry_carries_a_completename_and_an_id_teste():
    for entry in load_entity_map()["entities"]:
        assert entry["completename"].startswith("TIVIT")
        assert isinstance(entry["id_teste"], int)
        assert entry["clients"]


# -- 6. the id cross-check --------------------------------------------------


def test_crosscheck_reports_a_differing_id():
    resolver = EntityResolver(
        FakeGlpi({"tivit > grupo enel > brasil > enel sp": 41}),
        entity_map=ENTITY_MAP,
    )
    mismatches = {m.completename: m for m in resolver.crosscheck_ids()}
    item = mismatches["TIVIT > GRUPO ENEL > BRASIL > ENEL SP"]
    assert item.id_teste == 4
    assert item.resolved == 41


def test_crosscheck_is_quiet_when_the_ids_agree():
    resolver = EntityResolver(FakeGlpi(), entity_map=ENTITY_MAP)
    names = [m.completename for m in resolver.crosscheck_ids()]
    assert "TIVIT > GRUPO ENEL > BRASIL > ENEL SP" not in names
    assert "TIVIT > GRUPO ENEL > BRASIL > ENEL RJ" not in names

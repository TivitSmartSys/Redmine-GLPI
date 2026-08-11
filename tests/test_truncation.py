"""Plugin text columns are VARCHAR(255) - values over the limit are cut.

Diagnosed live 2026-08-11 on RDM 17444: 480 characters in "Andamento do
Projeto" made POST /Project answer

    ERROR_GLPI_ADD "Data too long for column 'andamentodoprojetofield' (1406)"

and - the part that makes it dangerous - GLPI kept the project it had already
committed, leaving it with no container row and therefore no rdmfield marker.
A sweep of trackers 14 and 42 found 92 of 5589 issues over the limit.

What is pinned here:

  1. a plugin text column is cut at the limit, and the record says so;
  2. a CORE column is never cut - Project.content is TEXT and truncating it
     would lose data for no reason at all;
  3. truncation does not move the section-7 arithmetic. The value did reach
     GLPI, so it stays in the WRITTEN bucket;
  4. the report shows both halves - what was kept and what was dropped.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import PLUGIN_TEXT_MAX_LENGTH  # noqa: E402
from report.reporter import ProjectPlan, Reporter  # noqa: E402
from transform.mapper import Mapper, Outcome  # noqa: E402

LONG = "a" * 300
TAIL = "a" * (300 - PLUGIN_TEXT_MAX_LENGTH)

MAPPING = {
    "project_core": [
        {"column": "content", "transform": "text",
         "sources": [{"from": "attribute", "name": "description"}]},
    ],
    "container15": [
        {"column": "andamentodoprojetofield", "transform": "text",
         "sources": [{"from": "custom_field", "name": "Andamento do Projeto"}]},
    ],
}


def issue_with(long_value: str) -> dict:
    return {
        "id": 17444,
        "subject": "ENEL RJ | Subestação Magé | Refresh",
        "tracker": {"id": 14, "name": "Projeto"},
        "description": long_value,
        "custom_fields": [{"name": "Andamento do Projeto", "value": long_value}],
    }


def mapped():
    mapper = Mapper(mapping=MAPPING, status_resolver=None, user_resolver=None,
                    dropdown_resolver=None)
    return mapper.map_project(issue_with(LONG))


def test_plugin_text_column_is_cut_at_the_limit():
    _core, container = mapped()
    value = container.payload["andamentodoprojetofield"]
    assert len(value) == PLUGIN_TEXT_MAX_LENGTH

    record = container.records[0]
    assert record.truncated
    assert record.original_length == 300
    assert record.outcome is Outcome.WRITTEN  # it DID reach GLPI
    assert "255" in record.detail


def test_core_column_is_never_cut():
    """Project.content is TEXT. Cutting it would lose data for no reason."""
    core, _container = mapped()
    assert core.payload["content"] == LONG
    assert not core.records[0].truncated


def test_value_at_the_limit_is_left_alone():
    mapper = Mapper(mapping=MAPPING, status_resolver=None, user_resolver=None,
                    dropdown_resolver=None)
    _core, container = mapper.map_project(issue_with("b" * PLUGIN_TEXT_MAX_LENGTH))
    assert not container.records[0].truncated
    assert not container.records[0].original_length


def plan_with(core, container) -> ProjectPlan:
    return ProjectPlan(issue=issue_with(LONG), core=core, container15=container)


def test_truncation_does_not_move_the_integrity_total():
    """Section 7 counts four buckets. A truncated field is still WRITTEN."""
    core, container = mapped()
    text = Reporter(plan_with(core, container)).render()
    assert "2 + 0 + 0 + 0 = 2" in text
    assert "nenhum campo foi descartado" in text or "[OK]" in text


def test_report_shows_what_was_kept_and_what_was_lost():
    core, container = mapped()
    text = Reporter(plan_with(core, container)).render()
    assert "CAMPOS CORTADOS PARA CABER NO GLPI" in text
    assert "300 caracteres cortados para 255" in text
    # Both halves are visible, so the reader can judge the loss.
    assert f"gravado: {'a' * PLUGIN_TEXT_MAX_LENGTH}" in text
    assert f"perdido: {TAIL}" in text


def test_block_says_so_when_nothing_was_cut():
    mapper = Mapper(mapping=MAPPING, status_resolver=None, user_resolver=None,
                    dropdown_resolver=None)
    core, container = mapper.map_project(issue_with("curto"))
    text = Reporter(plan_with(core, container)).render()
    assert "CAMPOS CORTADOS PARA CABER NO GLPI" in text
    assert "(nenhum)" in text

"""The summary cards must never disagree with the report they sit above.

Both count the same FieldRecord objects, so these tests pin the two together:
if summarise() ever starts counting something else, the numbers on screen would
contradict the integrity proof printed underneath them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from report.reporter import (  # noqa: E402
    PlannedFaturamento,
    PlannedTask,
    ProjectPlan,
    Reporter,
    SkippedChild,
)
from transform.mapper import FieldRecord, MappingResult, Outcome  # noqa: E402
from web.summary import summarise  # noqa: E402


def record(outcome: Outcome, **kwargs) -> FieldRecord:
    return FieldRecord(source_label=kwargs.pop("label", "Campo"), outcome=outcome, **kwargs)


def issue(issue_id: int = 20238, subject: str = "Projeto de teste") -> dict:
    return {
        "id": issue_id,
        "subject": subject,
        "tracker": {"id": 14, "name": "Projeto"},
        "custom_fields": [],
    }


def build_plan() -> ProjectPlan:
    core = MappingResult(
        payload={"name": "Projeto de teste"},
        records=[
            record(Outcome.WRITTEN, target_column="name"),
            record(Outcome.EMPTY_SOURCE, target_column="comment"),
        ],
    )
    container = MappingResult(
        payload={"rdmfield": "20238"},
        records=[
            record(Outcome.WRITTEN, target_column="rdmfield"),
            record(Outcome.UNRESOLVED, target_column="plugin_fields_gestofielddropdowns_id",
                   raw_value="Interna", mandatory=True),
            record(Outcome.NO_COUNTERPART, raw_value="algum valor"),
        ],
    )
    task = PlannedTask(
        issue=issue(20239, "Tarefa"),
        result=MappingResult(records=[record(Outcome.WRITTEN, target_column="name")]),
        depth=1,
        parent_redmine_id=20238,
    )
    invoice = PlannedFaturamento(
        issue=issue(20389, "Faturamento"),
        result=MappingResult(records=[record(Outcome.EMPTY_SOURCE, target_column="nnffield")]),
        origin="relation",
    )
    return ProjectPlan(
        issue=issue(),
        core=core,
        container15=container,
        never_write=[record(Outcome.NEVER_WRITE, target_column="prioridadefield")],
        tasks=[task],
        faturamento=[invoice],
        skipped_children=[
            SkippedChild(issue_id=19073, tracker_id=41, tracker_name="Compras",
                         subject="Compra", reason="tracker fora do escopo")
        ],
        tree_cycles=[123],
    )


def test_counts_every_bucket():
    summary = summarise(build_plan())
    assert summary["fields"] == {
        "total": 7,
        "written": 3,
        "empty_source": 2,
        "no_counterpart": 1,
        "unresolved": 1,
    }
    assert summary["integrity_ok"] is True


def test_ignored_excludes_fields_that_were_empty_at_the_source():
    """An empty source field is not a loss and must not inflate 'ignorados'."""
    summary = summarise(build_plan())
    assert summary["ignored"] == 2  # no_counterpart + unresolved, not empty_source


def test_totals_match_the_report_integrity_section():
    plan = build_plan()
    summary = summarise(plan)
    report = Reporter(plan, apply_mode=False).render()
    assert f"Campos de origem analisados : {summary['fields']['total']}" in report
    assert f"gravados no GLPI          : {summary['fields']['written']}" in report
    assert f"não resolvidos            : {summary['fields']['unresolved']}" in report


def test_reports_tree_and_scope_warnings():
    summary = summarise(build_plan())
    assert summary["skipped_children"] == 1
    assert summary["tree_cycles"] == 1
    assert summary["missing_mandatory"] == 1
    assert summary["never_write"] == 1


def test_dry_run_has_no_glpi_ids():
    summary = summarise(build_plan())
    assert summary["glpi_project_id"] is None
    assert summary["tasks"] == 1 and summary["tasks_written"] == 0
    assert summary["faturamento"] == 1 and summary["faturamento_written"] == 0


def test_integrity_failure_is_visible():
    """A record in no counted bucket means the migrator itself is broken."""
    plan = build_plan()
    plan.core.records.append(record(Outcome.NEVER_WRITE, target_column="prioridadefield"))
    assert summarise(plan)["integrity_ok"] is False

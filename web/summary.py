"""Card-level figures for the web panel, derived from a ProjectPlan.

Deliberately reads the plan OBJECT, never the rendered report text. The report
is the primary functional requirement (spec section 10); parsing it to build a
dashboard would put a regex between the operator and the proof that no field was
dropped. Here every number comes from the same typed records the report itself
counts, so the cards cannot disagree with the text below them.

The cards summarise; they never replace. The full report is always rendered
verbatim next to them.
"""

from __future__ import annotations

from config.settings import MANDATORY_CONTAINER26_COLUMNS
from report.reporter import ProjectPlan
from transform.attachments import summarise as summarise_attachments
from transform.mapper import Outcome


def _outcome_counts(plan: ProjectPlan) -> dict[str, int]:
    """Same four buckets the integrity section counts (report section 7)."""
    counts = {
        Outcome.WRITTEN: 0,
        Outcome.EMPTY_SOURCE: 0,
        Outcome.NO_COUNTERPART: 0,
        Outcome.UNRESOLVED: 0,
    }
    records = plan.all_records()
    for record in records:
        if record.outcome in counts:
            counts[record.outcome] += 1
    return {
        "total": len(records),
        "written": counts[Outcome.WRITTEN],
        "empty_source": counts[Outcome.EMPTY_SOURCE],
        "no_counterpart": counts[Outcome.NO_COUNTERPART],
        "unresolved": counts[Outcome.UNRESOLVED],
    }


def summarise(plan: ProjectPlan) -> dict:
    """Everything the summary row and the warning chips need."""
    counts = _outcome_counts(plan)
    tracker = plan.issue.get("tracker") or {}

    # Two counts, never one. Container 15 is validated inside POST /Project, so
    # a gap there REFUSES the write; container 26 is a direct row write that
    # skips validation, so a gap there only leaves the row incomplete. Folding
    # them together would turn a blocker and a nuisance into the same chip.
    missing_mandatory = [
        *plan.core.missing_mandatory,
        *plan.container15.missing_mandatory,
    ]
    missing_mandatory_faturamento = []
    for item in plan.faturamento:
        if item.core is not None:
            missing_mandatory_faturamento.extend(item.core.missing_mandatory)
        missing_mandatory_faturamento.extend(item.result.missing_mandatory)
        # Mapped columns already contributed via missing_mandatory above; only
        # count the ones no mapping entry claims, or they are counted twice.
        claimed = {
            record.target_column
            for record in item.result.records
            if record.target_column
        }
        missing_mandatory_faturamento.extend(
            column
            for column in MANDATORY_CONTAINER26_COLUMNS
            if column not in item.result.payload and column not in claimed
        )
    degraded = [item for item in plan.faturamento if not item.written]
    # Placeholder entries (an issue we could not read) carry no attachment id
    # and are excluded, exactly as report section 8 excludes them.
    attachment_counts = summarise_attachments(
        [item for item in plan.attachments if item.attachment_id]
    )

    return {
        "issue_id": plan.issue_id,
        "subject": plan.issue.get("subject") or "",
        "tracker": f"{tracker.get('id', '')} {tracker.get('name') or ''}".strip(),
        "glpi_project_id": plan.glpi_project_id,
        "tasks": len(plan.tasks),
        "tasks_written": sum(1 for task in plan.tasks if task.glpi_id),
        "faturamento": len(plan.faturamento),
        "faturamento_written": sum(1 for item in plan.faturamento if item.glpi_id),
        "faturamento_degraded": len(degraded),
        "fields": counts,
        # "ignorados" on the card = fields that carried a value in Redmine and
        # did not reach GLPI. Empty source fields are not a loss, so they are
        # counted separately and never folded into this number.
        "ignored": counts["no_counterpart"] + counts["unresolved"],
        "unresolved": counts["unresolved"],
        "missing_mandatory": len(missing_mandatory),
        "missing_mandatory_faturamento": len(missing_mandatory_faturamento),
        "never_write": len(plan.never_write),
        # Attachments have their own counters for the same reason they have
        # their own report section: they are files, not fields, and folding them
        # into the field arithmetic would break the one number the report
        # exists to guarantee.
        "attachments": attachment_counts["total"],
        "attachments_pending": attachment_counts["pending"],
        "attachments_done": attachment_counts["done"],
        "attachments_skipped": attachment_counts["skipped"],
        "attachments_failed": attachment_counts["failed"],
        "attachments_bytes": attachment_counts["bytes"],
        "skipped_children": len(plan.skipped_children),
        "ignored_relations": len(plan.ignored_relations),
        "tree_failures": len(plan.tree_failures),
        "tree_cycles": len(plan.tree_cycles),
        # The report proves this arithmetically; repeated here so the panel can
        # refuse to offer the write button when the migrator itself is broken.
        "integrity_ok": (
            counts["written"]
            + counts["empty_source"]
            + counts["no_counterpart"]
            + counts["unresolved"]
        )
        == counts["total"],
    }

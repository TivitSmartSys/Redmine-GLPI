"""C1 (fix wave 2026-08-27): apply_plan step 1 must not create a duplicate
project on retry.

CLAUDE.md documents the case this closes: a plugin `text` column over
VARCHAR(255) makes POST /Project succeed while the plugin's own INSERT for
container 15 fails (RDM 17444 -> project 1279), leaving a live project with no
container-15 row and therefore no rdmfield marker. batch/runner.py marks that
item `failed`, store/batch.py lists `failed` in RETRYABLE, and --resume
re-queues it untouched. Steps 3 and 4 already guard their own writes with
`store.lookup`; step 1 did not, so the retry could not find the marker in GLPI
and created a SECOND project - an empty shell, because store.lookup DOES hit
for the tasks written on attempt 1 and skips every one of them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from report.reporter import ProjectPlan  # noqa: E402
from transform.mapper import FieldRecord, MappingResult, Outcome  # noqa: E402


def issue(issue_id: int, tracker_id: int = 14) -> dict:
    return {
        "id": issue_id,
        "subject": f"Issue {issue_id}",
        "tracker": {"id": tracker_id, "name": f"Tracker {tracker_id}"},
        "custom_fields": [],
    }


def minimal_plan(issue_id: int = 16467) -> ProjectPlan:
    """A plan with no tasks and no faturamento - only step 1 and 2 matter here."""
    return ProjectPlan(
        issue=issue(issue_id),
        core=MappingResult(
            payload={"name": "Projeto"},
            records=[FieldRecord(source_label="Assunto", outcome=Outcome.WRITTEN,
                                 target_column="name")],
        ),
        container15=MappingResult(
            payload={"rdmfield": str(issue_id)},
            records=[FieldRecord(source_label="rdmfield", outcome=Outcome.WRITTEN,
                                 target_column="rdmfield")],
        ),
    )


class RecordingStore:
    """Minimal stand-in for store.db.MigrationStore - enough for apply_plan."""

    def __init__(self, existing=None):
        self._existing: dict[tuple[int, str], int] = dict(existing or {})
        self.records: list[tuple[int, int, str]] = []

    def lookup(self, redmine_id, itemtype):
        glpi_id = self._existing.get((int(redmine_id), itemtype))
        if glpi_id is None:
            return None
        return type("Entry", (), {"glpi_id": glpi_id})()

    def record(self, redmine_id, glpi_id, itemtype, **_kwargs):
        self.records.append((int(redmine_id), int(glpi_id), itemtype))
        self._existing[(int(redmine_id), itemtype)] = int(glpi_id)


class CountingGlpi:
    """Just enough of GlpiClient for apply_plan's steps 1 and 2."""

    def __init__(self):
        self.project_posts = 0
        self.container15_writes = 0

    def create_project(self, _payload):
        self.project_posts += 1
        return 1265

    def write_additional_fields_row(self, _project_id, _values):
        self.container15_writes += 1
        return 1


def test_a_retry_with_a_populated_store_does_not_post_a_second_project():
    glpi = CountingGlpi()
    store = RecordingStore()

    main.apply_plan(glpi, minimal_plan(), store, redmine=None)
    assert glpi.project_posts == 1

    # The retry: a fresh plan built the same way --resume rebuilds one, same
    # store, carrying the Project row recorded on attempt 1.
    retry_plan = minimal_plan()
    # No return value to assert on since 2026-09-10: apply_plan used to hand
    # back a bool its docstring described as a failure signal, but the only
    # return was True and no caller read it. A hard failure raises.
    main.apply_plan(glpi, retry_plan, store, redmine=None)

    assert glpi.project_posts == 1, "step 1 must be guarded exactly like steps 3 and 4"
    assert retry_plan.glpi_project_id == 1265
    assert retry_plan.glpi_ids[retry_plan.issue_id] == 1265


def test_first_run_records_the_project_in_the_local_store():
    glpi = CountingGlpi()
    store = RecordingStore()

    main.apply_plan(glpi, minimal_plan(), store, redmine=None)

    assert (16467, 1265, "Project") in store.records


def test_a_populated_store_hit_still_writes_the_container15_row():
    """Deduping the project must not skip step 2 - rdmfield is written on every
    run regardless of whether the project itself was just created or found."""
    glpi = CountingGlpi()
    store = RecordingStore(existing={(16467, "Project"): 1265})

    main.apply_plan(glpi, minimal_plan(), store, redmine=None)

    assert glpi.project_posts == 0
    assert glpi.container15_writes == 1

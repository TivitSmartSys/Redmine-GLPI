"""The batch ledger answers "what have we tried", not "what maps to what".

Deliberately separate from migration_map: that table answers which Redmine id
became which GLPI id, and folding retry bookkeeping into it would make a failed
attempt look like a mapping.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from store.batch import (  # noqa: E402
    STATE_FAILED,
    STATE_OK,
    STATE_PENDING,
    STATE_SKIPPED,
    BatchLedger,
)


def test_queued_issues_start_pending(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2, 3])

        assert ledger.pending(run) == [1, 2, 3]


def test_marking_an_issue_removes_it_from_pending(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2, 3])
        ledger.mark(run, 2, STATE_OK)

        assert ledger.pending(run) == [1, 3]


def test_a_failure_is_pending_again_so_resume_retries_it(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2])
        ledger.mark(run, 1, STATE_FAILED, "erro 500")

        assert ledger.pending(run) == [2, 1]
        assert ledger.failures(run) == [(1, "erro 500")]


def test_a_skipped_issue_is_not_retried(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2])
        ledger.mark(run, 1, STATE_SKIPPED, "já migrado")

        assert ledger.pending(run) == [2]


def test_counts_close_the_arithmetic(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2, 3, 4])
        ledger.mark(run, 1, STATE_OK)
        ledger.mark(run, 2, STATE_FAILED, "x")
        ledger.mark(run, 3, STATE_SKIPPED, "y")

        counts = ledger.counts(run)

        assert counts[STATE_OK] == 1
        assert counts[STATE_FAILED] == 1
        assert counts[STATE_SKIPPED] == 1
        assert counts[STATE_PENDING] == 1
        assert sum(counts.values()) == 4


def test_two_runs_do_not_see_each_other(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        first = ledger.start_run("hydro")
        second = ledger.start_run("cemig")
        ledger.queue(first, [1])
        ledger.queue(second, [2])

        assert ledger.pending(first) == [1]
        assert ledger.pending(second) == [2]


def test_runs_lists_every_run_with_its_counts(tmp_path):
    """The panel needs the whole picture of a run without replaying it.

    `pending()` answers "what is left"; the resume list has to say what a run
    was FOR (its project) and how it went, which no other method carries.
    """
    with BatchLedger(tmp_path / "b.db") as ledger:
        first = ledger.start_run("hydro")
        ledger.queue(first, [1, 2, 3])
        ledger.mark(first, 1, STATE_OK)
        ledger.mark(first, 2, STATE_FAILED, "erro 500")
        second = ledger.start_run("operacao-cemig")
        ledger.queue(second, [9])

        runs = ledger.runs()

        assert [run["run_id"] for run in runs] == [second, first]  # newest first
        assert runs[1]["label"] == "hydro"
        assert runs[1]["counts"][STATE_OK] == 1
        assert runs[1]["counts"][STATE_FAILED] == 1
        assert runs[1]["counts"][STATE_PENDING] == 1
        assert runs[1]["total"] == 3
        # Resumable == pending + failed, the same pair pending() retries.
        assert runs[1]["resumable"] == 2
        assert runs[0]["label"] == "operacao-cemig"


def test_a_run_with_no_queued_items_still_appears(tmp_path):
    """A run that crashed before queueing must not vanish from the list."""
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")

        runs = ledger.runs()

        assert [entry["run_id"] for entry in runs] == [run]
        assert runs[0]["total"] == 0
        assert runs[0]["resumable"] == 0


def test_run_label_answers_which_project_a_run_belongs_to(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("projetos-telecom")

        assert ledger.run_label(run) == "projetos-telecom"
        assert ledger.run_label("nao-existe") is None


def test_items_replays_a_finished_run_from_the_ledger(tmp_path):
    """A finished run must be readable after the process that ran it is gone.

    The panel's progress table is built from live events; the ledger is the only
    thing that can rebuild it afterwards, and it is what carries each failure's
    reason - the thing an operator comes back for.
    """
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [10, 20, 30])
        ledger.mark(run, 10, STATE_OK)
        ledger.mark(run, 20, STATE_FAILED, "erro 500")

        items = ledger.items(run)

        assert [item["issue_id"] for item in items] == [10, 20, 30]  # queue order
        assert [item["state"] for item in items] == [
            STATE_OK, STATE_FAILED, STATE_PENDING,
        ]
        assert items[1]["detail"] == "erro 500"


def test_items_of_an_unknown_run_is_empty_not_an_error(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        assert ledger.items("nao-existe") == []

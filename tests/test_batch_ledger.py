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

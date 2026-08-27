"""The aggregate must sum to the total and name every failure.

Same contract as spec 13 one level up: the per-item reports prove no field
vanished; this one proves no item vanished.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from batch.report import render_summary  # noqa: E402
from store.batch import STATE_FAILED, STATE_OK, STATE_SKIPPED, BatchLedger  # noqa: E402


@pytest.fixture()
def populated(tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [10, 11, 12, 13])
        ledger.mark(run, 10, STATE_OK)
        ledger.mark(run, 11, STATE_FAILED, "arquivo grande demais")
        ledger.mark(run, 12, STATE_SKIPPED, "já migrado")
        yield ledger, run


def test_summary_counts_add_up_to_the_queued_total(populated):
    ledger, run = populated

    text = render_summary(ledger, run, "hydro")

    assert "4" in text
    assert sum(ledger.counts(run).values()) == 4


def test_every_failure_is_named_with_its_reason(populated):
    ledger, run = populated

    text = render_summary(ledger, run, "hydro")

    assert "11" in text
    assert "arquivo grande demais" in text


def test_the_purge_record_is_carried_into_the_final_report(populated):
    ledger, run = populated

    text = render_summary(ledger, run, "hydro", purge_record="LIMPEZA: 1265 removidos")

    assert "LIMPEZA: 1265 removidos" in text

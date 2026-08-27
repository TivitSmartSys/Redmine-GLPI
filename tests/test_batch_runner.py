"""One failing item must not end a run of thousands.

The whole point of the batch is that it finishes. Stopping on the first 33 MB
attachment or the first 403 from Redmine means never reaching item 5000.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from batch import runner  # noqa: E402
from store.batch import STATE_FAILED, STATE_OK, STATE_SKIPPED, BatchLedger  # noqa: E402


class Boom(Exception):
    pass


@pytest.fixture()
def ledger(tmp_path):
    with BatchLedger(tmp_path / "b.db") as led:
        yield led


def patch_pipeline(monkeypatch, *, fails=(), migrated=()):
    """Replace the three main.py entry points the runner calls."""
    applied = []

    def fake_check(glpi, issue_id):
        return 999 if issue_id in migrated else None

    def fake_plan(glpi, redmine, mapping, issue_id, **kwargs):
        if issue_id in fails:
            raise Boom(f"falhou em {issue_id}")
        return object()

    def fake_apply(glpi, plan, store, redmine=None):
        applied.append(plan)

    monkeypatch.setattr(runner, "check_already_migrated", fake_check)
    monkeypatch.setattr(runner, "build_project_plan", fake_plan)
    monkeypatch.setattr(runner, "apply_plan", fake_apply)
    monkeypatch.setattr(runner, "render_item_report", lambda plan, apply_mode: "relatório")
    return applied


def test_a_failing_item_does_not_stop_the_run(monkeypatch, ledger, tmp_path):
    patch_pipeline(monkeypatch, fails={2})
    run = ledger.start_run("t")
    ledger.queue(run, [1, 2, 3])

    runner.run_batch(
        None, None, {}, ledger, run, [1, 2, 3],
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_OK] == 2
    assert counts[STATE_FAILED] == 1
    assert ledger.failures(run)[0][0] == 2


def test_an_already_migrated_item_is_skipped_not_failed(monkeypatch, ledger, tmp_path):
    patch_pipeline(monkeypatch, migrated={2})
    run = ledger.start_run("t")
    ledger.queue(run, [1, 2])

    runner.run_batch(
        None, None, {}, ledger, run, [1, 2],
        apply_mode=True, report_dir=tmp_path,
    )

    assert ledger.counts(run)[STATE_SKIPPED] == 1
    assert ledger.counts(run)[STATE_OK] == 1


def test_dry_run_never_calls_apply(monkeypatch, ledger, tmp_path):
    applied = patch_pipeline(monkeypatch)
    run = ledger.start_run("t")
    ledger.queue(run, [1, 2])

    runner.run_batch(
        None, None, {}, ledger, run, [1, 2],
        apply_mode=False, report_dir=tmp_path,
    )

    assert applied == [], "dry-run must write nothing"


def test_every_item_gets_its_own_report_file(monkeypatch, ledger, tmp_path):
    patch_pipeline(monkeypatch)
    run = ledger.start_run("t")
    ledger.queue(run, [1, 2])

    runner.run_batch(
        None, None, {}, ledger, run, [1, 2],
        apply_mode=True, report_dir=tmp_path,
    )

    assert (tmp_path / "RDM1.txt").exists()
    assert (tmp_path / "RDM2.txt").exists()

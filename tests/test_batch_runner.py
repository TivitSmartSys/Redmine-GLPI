"""One failing item must not end a run of thousands.

The whole point of the batch is that it finishes. Stopping on the first 33 MB
attachment or the first 403 from Redmine means never reaching item 5000.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import migrate_batch  # noqa: E402
from batch import runner  # noqa: E402
from store.batch import (  # noqa: E402
    STATE_FAILED,
    STATE_OK,
    STATE_PENDING,
    STATE_SKIPPED,
    BatchLedger,
)


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


# -- I3: a dead GLPI session must not manufacture thousands of fake failures --


def test_consecutive_failures_trip_the_abort_and_leave_the_rest_pending(
    monkeypatch, ledger, tmp_path
):
    """The scenario CLAUDE.md/the finding describes: the session dies partway
    through and every remaining item would otherwise fail fast, landing in the
    ledger as genuine migration failures indistinguishable from real ones."""
    total = runner.MAX_CONSECUTIVE_FAILURES + 5
    ids = list(range(1, total + 1))
    patch_pipeline(monkeypatch, fails=set(ids))
    run = ledger.start_run("t")
    ledger.queue(run, ids)

    runner.run_batch(
        None, None, {}, ledger, run, ids,
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_FAILED] == runner.MAX_CONSECUTIVE_FAILURES
    # Everything past the trip point keeps its `pending` state - it is never
    # marked - so --resume retries it untouched once the session is back.
    assert counts.get(STATE_PENDING, 0) == total - runner.MAX_CONSECUTIVE_FAILURES


def test_a_success_between_failures_resets_the_counter(monkeypatch, ledger, tmp_path):
    """Sparse, unrelated per-item failures (an oversized file, a 403, a missing
    entity) must never trip the abort - only a true unbroken run of them."""
    n = runner.MAX_CONSECUTIVE_FAILURES - 1
    bad_1 = list(range(1, n + 1))
    bad_2 = list(range(1000, 1000 + n))
    ids = bad_1 + [999] + bad_2
    patch_pipeline(monkeypatch, fails=set(bad_1) | set(bad_2))
    run = ledger.start_run("t")
    ledger.queue(run, ids)

    runner.run_batch(
        None, None, {}, ledger, run, ids,
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_FAILED] == len(bad_1) + len(bad_2)
    assert counts[STATE_OK] == 1
    assert counts.get(STATE_PENDING, 0) == 0, "the run must not have aborted"


def test_a_skip_also_resets_the_counter(monkeypatch, ledger, tmp_path):
    n = runner.MAX_CONSECUTIVE_FAILURES - 1
    bad_1 = list(range(1, n + 1))
    bad_2 = list(range(1000, 1000 + n))
    ids = bad_1 + [999] + bad_2
    patch_pipeline(monkeypatch, fails=set(bad_1) | set(bad_2), migrated={999})
    run = ledger.start_run("t")
    ledger.queue(run, ids)

    runner.run_batch(
        None, None, {}, ledger, run, ids,
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_FAILED] == len(bad_1) + len(bad_2)
    assert counts[STATE_SKIPPED] == 1
    assert counts.get(STATE_PENDING, 0) == 0, "the run must not have aborted"


def test_fewer_than_the_threshold_does_not_abort(monkeypatch, ledger, tmp_path):
    ids = list(range(1, runner.MAX_CONSECUTIVE_FAILURES))
    patch_pipeline(monkeypatch, fails=set(ids))
    run = ledger.start_run("t")
    ledger.queue(run, ids)

    runner.run_batch(
        None, None, {}, ledger, run, ids,
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_FAILED] == len(ids)
    assert counts.get(STATE_PENDING, 0) == 0


# -- M8: a report-file write failure must not read as a migration failure ----


def test_a_report_write_failure_does_not_mark_the_item_failed(
    monkeypatch, ledger, tmp_path
):
    """A disk-full mid-run used to be caught by the pipeline's own broad
    except and marked `failed`, even though the migration itself - already
    committed to GLPI - had succeeded."""
    patch_pipeline(monkeypatch)
    run = ledger.start_run("t")
    ledger.queue(run, [1])

    def boom_write_text(self, *_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom_write_text)

    runner.run_batch(
        None, None, {}, ledger, run, [1],
        apply_mode=True, report_dir=tmp_path,
    )

    counts = ledger.counts(run)
    assert counts[STATE_OK] == 1
    assert counts.get(STATE_FAILED, 0) == 0
    assert not (tmp_path / "RDM1.txt").exists()


def test_dry_run_is_the_default():
    args = migrate_batch.build_parser().parse_args(["--project", "hydro"])

    assert args.apply is False
    assert args.resume is False


def test_project_choice_is_restricted_to_the_three_that_have_roots():
    parser = migrate_batch.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--project", "configuracao-rede-corp-voip"])


def test_limit_and_skips_are_passed_through():
    args = migrate_batch.build_parser().parse_args(
        ["--project", "hydro", "--limit", "50", "--skip-attachments"]
    )

    assert args.limit == 50
    assert args.skip_attachments is True

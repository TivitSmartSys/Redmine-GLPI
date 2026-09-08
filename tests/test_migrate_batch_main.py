"""migrate_batch.py's CLI-level failure handling around --resume and
--purge-record (fix wave 2026-08-27, findings M9 and M11).

Both scenarios run through main() with GlpiClient/RedmineClient replaced by
harmless fakes and the main.py pipeline entry points stubbed out (the same
pattern tests/test_batch_runner.py uses) - no network call is ever made, so
this does not touch a live instance.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import migrate_batch  # noqa: E402
from batch import runner  # noqa: E402
from store.batch import BatchLedger  # noqa: E402


class FakeSettings:
    glpi_url = "http://glpi"
    glpi_user_token = "u"
    glpi_app_token = "a"
    redmine_url = "http://redmine"
    redmine_api_key = "k"

    def secret_values(self):
        return ()


class FakeCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def patch_cli(monkeypatch):
    """Replace everything that would otherwise touch a live GLPI/Redmine."""
    monkeypatch.setattr(migrate_batch, "load_settings", lambda: FakeSettings())
    monkeypatch.setattr(migrate_batch, "load_yaml", lambda _name: {})
    monkeypatch.setattr(migrate_batch, "GlpiClient", lambda *a, **k: FakeCtx())
    monkeypatch.setattr(migrate_batch, "RedmineClient", lambda *a, **k: FakeCtx())
    monkeypatch.setattr(migrate_batch, "run_preflight", lambda *a, **k: True)


def patch_pipeline(monkeypatch):
    """Replace the three main.py entry points batch/runner.py calls."""
    monkeypatch.setattr(runner, "check_already_migrated", lambda glpi, issue_id: None)
    monkeypatch.setattr(
        runner, "build_project_plan",
        lambda glpi, redmine, mapping, issue_id, **kwargs: object(),
    )
    monkeypatch.setattr(runner, "apply_plan", lambda glpi, plan, store, redmine=None: None)
    monkeypatch.setattr(
        runner, "render_item_report", lambda plan, apply_mode: "relatório"
    )


# -- M11: --resume with an unknown run id must not read as a completed run --


def test_resume_with_unknown_run_id_is_a_config_error(monkeypatch, tmp_path, capsys):
    patch_cli(monkeypatch)
    db = tmp_path / "b.db"

    code = migrate_batch.main([
        "--project", "hydro", "--resume", "does-not-exist", "--db", str(db),
    ])

    assert code == migrate_batch.EXIT_CONFIG
    assert "does-not-exist" in capsys.readouterr().err


def test_resume_with_a_real_run_id_proceeds_as_before(monkeypatch, tmp_path):
    """The new check must not break the ordinary --resume path."""
    patch_cli(monkeypatch)
    patch_pipeline(monkeypatch)
    db = tmp_path / "b.db"
    reports = tmp_path / "reports"

    with BatchLedger(db) as ledger:
        run_id = ledger.start_run("hydro")
        ledger.queue(run_id, [1])

    code = migrate_batch.main([
        "--project", "hydro", "--resume", run_id, "--db", str(db),
        "--reports", str(reports),
    ])

    assert code == migrate_batch.EXIT_OK
    assert (reports / run_id / "resumo.txt").exists()


# -- M9: an unreadable --purge-record must not take the summary down --------


def test_unreadable_purge_record_is_reported_and_the_run_still_finishes(
    monkeypatch, tmp_path, capsys
):
    patch_cli(monkeypatch)
    patch_pipeline(monkeypatch)
    db = tmp_path / "b.db"
    reports = tmp_path / "reports"

    with BatchLedger(db) as ledger:
        run_id = ledger.start_run("hydro")
        ledger.queue(run_id, [1])

    bad_record = tmp_path / "purge.txt"
    # Continuation bytes with no lead byte: guaranteed invalid UTF-8, so
    # Path.read_text(encoding="utf-8") raises UnicodeDecodeError.
    bad_record.write_bytes(b"\x80\x81\x82")

    code = migrate_batch.main([
        "--project", "hydro", "--resume", run_id, "--db", str(db),
        "--reports", str(reports), "--purge-record", str(bad_record),
    ])

    assert code == migrate_batch.EXIT_OK
    err = capsys.readouterr().err
    assert str(bad_record) in err
    # The summary - the record of thousands of writes - must not be lost for
    # the sake of a nice-to-have appendix that could not be read.
    summary_path = reports / run_id / "resumo.txt"
    assert summary_path.exists()


def test_missing_purge_record_is_silently_skipped_not_reported(
    monkeypatch, tmp_path, capsys
):
    """A merely-missing file was already handled before this fix (`.exists()`
    guards it); only an UNREADABLE one - permission error, bad encoding - is
    the new case, and the two must not be conflated in the warning."""
    patch_cli(monkeypatch)
    patch_pipeline(monkeypatch)
    db = tmp_path / "b.db"
    reports = tmp_path / "reports"

    with BatchLedger(db) as ledger:
        run_id = ledger.start_run("hydro")
        ledger.queue(run_id, [1])

    code = migrate_batch.main([
        "--project", "hydro", "--resume", run_id, "--db", str(db),
        "--reports", str(reports),
        "--purge-record", str(tmp_path / "does-not-exist-at-all.txt"),
    ])

    assert code == migrate_batch.EXIT_OK
    assert "não pôde ser lido" not in capsys.readouterr().err

"""An error raised AFTER the session is open must not blame the credentials.

Found by the audit of 2026-09-10. `main()` wrapped the whole `with GlpiClient(...)`
block in one `except ApiError` that printed PREFLIGHT_SESSION_FAILED:

    [FALHA] Não foi possível iniciar a sessão no GLPI.
      Verifique GLPI_URL, GLPI_USER_TOKEN e GLPI_APP_TOKEN.

So a refused `POST /Project` sent the operator to check tokens that were
demonstrably fine - the session had just carried a full preflight and a whole
plan. CLAUDE.md documents the case that makes this expensive: a plugin `text`
column over VARCHAR(255) makes GLPI commit the project and THEN fail, leaving
an orphan project with no rdmfield marker. The one message the operator sees in
that exact situation pointed away from the cause.

The batch runner and the web panel already routed this correctly; only the
single-issue CLI misreported it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as cli  # noqa: E402
from clients.errors import GlpiError  # noqa: E402
from report import messages  # noqa: E402


class FakeSettings:
    glpi_url = "http://glpi/apirest.php"
    glpi_user_token = "user"
    glpi_app_token = "app"
    redmine_url = "http://redmine"
    redmine_api_key = "key"

    def secret_values(self):
        return ()


class FakeCtx:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class FakeReporter:
    def __init__(self, *_args, **_kwargs):
        pass

    def render(self):
        return "RELATÓRIO"

    def save(self, path):
        return path


def _base(monkeypatch):
    """Everything up to the write succeeds, exactly as in a real run."""
    monkeypatch.setattr(cli, "load_settings", lambda: FakeSettings())
    monkeypatch.setattr(cli, "load_yaml", lambda _name: {})
    monkeypatch.setattr(cli, "GlpiClient", lambda *a, **k: FakeCtx())
    monkeypatch.setattr(cli, "RedmineClient", lambda *a, **k: FakeCtx())
    monkeypatch.setattr(cli, "run_preflight", lambda *a, **k: True)
    monkeypatch.setattr(cli, "check_already_migrated", lambda *a, **k: None)
    monkeypatch.setattr(cli, "build_project_plan", lambda *a, **k: object())
    monkeypatch.setattr(cli, "Reporter", FakeReporter)
    monkeypatch.setattr(cli, "MigrationStore", lambda _path: FakeCtx())


def test_a_refused_write_is_not_reported_as_a_session_problem(monkeypatch, capsys):
    _base(monkeypatch)

    def boom(*_args, **_kwargs):
        raise GlpiError("ERROR_GLPI_ADD Data too long for column 'x' (1406)")

    monkeypatch.setattr(cli, "apply_plan", boom)

    code = cli.main(["--issue", "20238", "--apply", "--yes"])
    err = capsys.readouterr().err

    assert code == cli.EXIT_FAILED
    assert "GLPI_USER_TOKEN" not in err


def test_a_refused_write_shows_the_glpi_detail(monkeypatch, capsys):
    _base(monkeypatch)

    def boom(*_args, **_kwargs):
        raise GlpiError("ERROR_GLPI_ADD Data too long for column 'x' (1406)")

    monkeypatch.setattr(cli, "apply_plan", boom)

    cli.main(["--issue", "20238", "--apply", "--yes"])

    assert "Data too long" in capsys.readouterr().err


def test_a_failure_while_planning_is_routed_the_same_way(monkeypatch, capsys):
    """Reading the tree can fail too, and it is not a credentials problem
    either once the session has been established."""
    _base(monkeypatch)

    def boom(*_args, **_kwargs):
        raise GlpiError("ERROR_GLPI_SEARCH")

    monkeypatch.setattr(cli, "build_project_plan", boom)

    code = cli.main(["--issue", "20238"])

    assert code == cli.EXIT_FAILED
    assert "GLPI_USER_TOKEN" not in capsys.readouterr().err


def test_a_session_that_cannot_open_still_blames_the_credentials(monkeypatch, capsys):
    """The original message must survive for the case it was written for."""
    _base(monkeypatch)

    def boom(*_args, **_kwargs):
        raise GlpiError("initSession: 401")

    monkeypatch.setattr(cli, "GlpiClient", boom)

    code = cli.main(["--issue", "20238"])

    assert code == cli.EXIT_FAILED
    assert "GLPI_USER_TOKEN" in capsys.readouterr().err

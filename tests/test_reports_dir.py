"""Onde os artefatos de uma execução são gravados, e o que acontece quando não dá.

Diagnosticado 2026-09-10 na VM de produção (Ubuntu 24.04, systemd, o serviço
descrito em DEPLOY.md §7): clicar em "Atualizar" na aba Progresso morreu com

    Erro inesperado: [Errno 30] Read-only file system: 'reports'

depois de ~100 s já gastos lendo o Redmine. Três defeitos, não um:

1. `_run_status` gravava pelo caminho de MÓDULO (`migration_status.CACHE_PATH`,
   relativo ao CWD) em vez do `self._reports_dir` que todo o resto de jobs.py
   usa. Esse é o defeito próprio da aba.

2. Corrigir só isso não resolveria a produção: `wsgi.py` nunca passa
   `reports_dir`, então o valor configurado é o mesmo "reports" relativo,
   dentro de `/opt/redmine-glpi/app`, que o systemd monta somente-leitura. O
   **Lote tem exatamente a mesma falha** - `batch/runner.py` faz o mesmo mkdir.

3. A falha chegava como `Errno 30` cru, depois do trabalho caro, e sem dizer o
   que fazer. O diretório é verificável em 1 ms, antes de qualquer leitura.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import migration_status as status  # noqa: E402
from report import messages  # noqa: E402
from web import jobs as jobs_module  # noqa: E402
from web.jobs import Job, JobManager, ReportsDirUnwritable  # noqa: E402

REQUIRED_ENV = {
    "REDMINE_URL": "https://redmine.invalid",
    "REDMINE_API_KEY": "chave-redmine-de-teste",
    "GLPI_URL": "https://glpi.invalid/apirest.php",
    "GLPI_USER_TOKEN": "token-usuario-de-teste",
    "GLPI_APP_TOKEN": "token-app-de-teste",
}


@pytest.fixture()
def settings(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    from config.settings import load_settings

    return load_settings()


def _manager(settings, tmp_path, reports_dir) -> JobManager:
    return JobManager(settings, {}, str(tmp_path / "b.db"), str(reports_dir))


class _StubClient:
    """Opens and closes; reads nothing. The reads themselves are patched out."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# -- o caminho configurado é o que vale -------------------------------------


def test_o_cache_segue_o_diretorio_configurado(settings, tmp_path, monkeypatch):
    """A aba grava onde o JobManager foi mandado gravar, não em "reports"."""
    reports = tmp_path / "estado" / "reports"
    manager = _manager(settings, tmp_path, reports)

    gravado = {}

    def _fake_save(rows, scope=None, imported=None, path=None):
        gravado["path"] = path

    monkeypatch.setattr(status, "save_cache", _fake_save)
    monkeypatch.setattr(status, "collect", lambda *a, **k: [])
    monkeypatch.setattr(status, "measure_scope", lambda redmine: {})
    monkeypatch.setattr(status, "measure_imported", lambda glpi, rows: 0)
    monkeypatch.setattr(jobs_module, "GlpiClient", _StubClient)
    monkeypatch.setattr(jobs_module, "RedmineClient", _StubClient)

    manager._run_status(Job(kind="status", label="teste"), verify=False)

    assert gravado["path"] == reports / status.CACHE_FILENAME
    assert "estado" in str(gravado["path"])


def test_a_pagina_le_do_diretorio_configurado(monkeypatch, tmp_path):
    """De nada adianta gravar no lugar certo e ler no errado."""
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    reports = tmp_path / "estado" / "reports"
    reports.mkdir(parents=True)
    status.save_cache([], {"hydro": ("tracker 42", 183)}, 789,
                      path=reports / status.CACHE_FILENAME)

    from web.server import create_app

    app = create_app(db_path=str(tmp_path / "b.db"), reports_dir=str(reports))
    app.config["TESTING"] = True
    with app.test_client() as client:
        corpo = client.get("/api/status/meta").get_json()
    assert corpo["scope"] == 183
    assert corpo["imported"] == 789


# -- falhar cedo, e falhar dizendo o quê ------------------------------------


class _DummyClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("nenhuma sessão deve ser aberta antes da verificação")

    def __enter__(self):  # pragma: no cover - nunca alcançado
        return self

    def __exit__(self, *exc):  # pragma: no cover
        return False


def test_diretorio_ilegivel_falha_antes_de_qualquer_leitura(settings, tmp_path, monkeypatch):
    """O erro custava ~100 s de leitura do Redmine. Agora custa 1 ms.

    O diretório fica sob um ARQUIVO, então o mkdir falha de verdade no nível
    do SO - o mesmo tipo de OSError que o EROFS da produção, sem precisar
    montar nada somente-leitura no teste.
    """
    bloqueio = tmp_path / "arquivo"
    bloqueio.write_text("", encoding="utf-8")
    manager = _manager(settings, tmp_path, bloqueio / "reports")

    monkeypatch.setattr(jobs_module, "GlpiClient", _DummyClient)
    monkeypatch.setattr(jobs_module, "RedmineClient", _DummyClient)

    with pytest.raises(ReportsDirUnwritable) as erro:
        manager._run_status(Job(kind="status", label="teste"), verify=False)

    detalhe = str(erro.value)
    assert "reports" in detalhe
    assert "MIGRATION_REPORTS_DIR" in detalhe


def test_a_mensagem_nao_e_um_errno_cru(settings, tmp_path):
    """O operador precisa saber o caminho e a saída, não o número do errno."""
    bloqueio = tmp_path / "arquivo"
    bloqueio.write_text("", encoding="utf-8")
    manager = _manager(settings, tmp_path, bloqueio / "reports")

    with pytest.raises(ReportsDirUnwritable) as erro:
        manager.ensure_reports_dir()

    assert messages.UI_REPORTS_DIR_UNWRITABLE.split("{")[0].strip() in str(erro.value)


def test_diretorio_bom_e_criado_e_devolvido(settings, tmp_path):
    reports = tmp_path / "novo" / "reports"
    manager = _manager(settings, tmp_path, reports)
    assert manager.ensure_reports_dir() == reports
    assert reports.is_dir()
    # A sonda não pode deixar lixo para trás.
    assert list(reports.iterdir()) == []


def test_o_navegador_recebe_a_instrucao_e_nao_um_errno(settings, tmp_path, monkeypatch):
    """O que o operador vê é o teste que importa.

    Antes: "Erro inesperado: [Errno 30] Read-only file system: 'reports'",
    que não diz onde nem o que fazer. `_guard` agora trata esta exceção como
    trata a ApiError - a mensagem já vem pronta em PT-BR.
    """
    bloqueio = tmp_path / "arquivo"
    bloqueio.write_text("", encoding="utf-8")
    manager = _manager(settings, tmp_path, bloqueio / "reports")

    monkeypatch.setattr(jobs_module, "GlpiClient", _DummyClient)
    monkeypatch.setattr(jobs_module, "RedmineClient", _DummyClient)

    job = manager.start_status(verify=False)
    job.thread.join(timeout=10)

    assert job.state == "failed"
    erros = [
        event.data
        for event in job.log.follow(0)
        if event is not None and event.type == "error"
    ]
    assert erros, "o job falhou sem emitir erro nenhum"
    assert "MIGRATION_REPORTS_DIR" in erros[0]
    assert "Erro inesperado" not in erros[0]


# -- a entrada de deployment ------------------------------------------------


def test_wsgi_aceita_o_diretorio_por_variavel_de_ambiente(monkeypatch, tmp_path):
    """O mesmo mecanismo que MIGRATION_DB_PATH já dá ao banco.

    Sem isto não existe deployment nenhum em que o Lote e a aba Progresso
    consigam gravar: o único valor possível é "reports" dentro do diretório
    da aplicação, que em produção é somente-leitura.
    """
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    destino = tmp_path / "var" / "reports"
    monkeypatch.setenv("MIGRATION_REPORTS_DIR", str(destino))
    monkeypatch.setenv("MIGRATION_DB_PATH", str(tmp_path / "b.db"))

    import importlib

    import wsgi

    importlib.reload(wsgi)
    assert wsgi.app.config["REPORTS_DIR"] == str(destino)

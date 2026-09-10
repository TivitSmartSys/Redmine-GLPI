"""A aba Progresso: a mesma página do CLI, servida a partir do cache.

Nada aqui re-testa os números - `tests/test_status_page.py` cobre a
aritmética e o renderizador. O que a camada web acrescenta são três coisas,
e são elas que estão cobertas:

1. A aba abre a partir do disco. Uma varredura de 684 projetos levou ~45 min
   em 2026-08-31; ler o GLPI a cada vez que alguém clica na aba é inviável e
   é justamente por isso que o cache existe.
2. Sem cache a resposta é um estado vazio explicando o que fazer, nunca um
   500 - a primeira coisa que um operador vê numa instalação nova é
   exatamente esse caso.
3. Atualizar é um job como qualquer outro e passa pelo mesmo portão: um job
   por vez. Uma varredura disparada durante um lote em gravação disputaria a
   sessão do GLPI com ele.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import migration_status as status  # noqa: E402
from report import messages  # noqa: E402

REQUIRED_ENV = {
    "REDMINE_URL": "https://redmine.invalid",
    "REDMINE_API_KEY": "chave-redmine-de-teste",
    "GLPI_URL": "https://glpi.invalid/apirest.php",
    "GLPI_USER_TOKEN": "token-usuario-de-teste",
    "GLPI_APP_TOKEN": "token-app-de-teste",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    from web.server import create_app

    app = create_app(db_path=str(tmp_path / "b.db"), reports_dir=str(tmp_path / "reports"))
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        test_client.application = app
        yield test_client


# O cache mora no diretório de relatórios CONFIGURADO, não num caminho de
# módulo. Ver tests/test_reports_dir.py: ler pelo padrão do módulo funciona
# na estação de trabalho e responde "sem leitura salva" para sempre em
# produção, onde o diretório da aplicação é somente-leitura.
def _cache_path(tmp_path):
    return tmp_path / "reports" / status.CACHE_FILENAME


def _write_cache(tmp_path, scope=None, imported=None, rows=None):
    linhas = rows if rows is not None else [
        status.Row(
            redmine_id=18586,
            glpi_id=1038,
            name="Projeto CEMIG de teste",
            entity="CEMIG D",
            created="2026-01-01 00:00:00",
            tasks=3,
            notes=1,
            documents=0,
            tracker=39,
        )
    ]
    status.save_cache(linhas, scope, imported, path=_cache_path(tmp_path))


# -- a página --------------------------------------------------------------


def test_a_aba_serve_a_pagina_gravada_no_cache(client, tmp_path):
    _write_cache(tmp_path, scope={"operacao-cemig": ("tracker 39", 6)}, imported=789)
    resposta = client.get("/api/status/page")
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "18586" in corpo and "1038" in corpo
    assert "789" in corpo


def test_sem_cache_a_aba_explica_em_vez_de_quebrar(client):
    resposta = client.get("/api/status/page")
    assert resposta.status_code == 200
    assert messages.UI_STATUS_EMPTY in resposta.get_data(as_text=True)


def test_o_cache_ilegivel_nao_derruba_a_aba(client, tmp_path):
    caminho = _cache_path(tmp_path)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text("{lixo", encoding="utf-8")
    resposta = client.get("/api/status/page")
    assert resposta.status_code == 200
    assert messages.UI_STATUS_EMPTY in resposta.get_data(as_text=True)


# -- os números do cabeçalho -----------------------------------------------


def test_meta_traz_os_numeros_e_a_data_da_leitura(client, tmp_path):
    _write_cache(tmp_path, scope={"hydro": ("tracker 42", 183)}, imported=789)
    corpo = client.get("/api/status/meta").get_json()
    assert corpo["projects"] == 1
    assert corpo["scope"] == 183
    assert corpo["remaining"] == 182
    assert corpo["imported"] == 789
    assert corpo["saved_at"]


def test_meta_sem_cache_diz_que_nao_ha_leitura(client):
    corpo = client.get("/api/status/meta").get_json()
    assert corpo["saved_at"] is None
    assert corpo["projects"] == 0


def test_meta_nao_inventa_zero_para_o_que_nao_foi_medido(client, tmp_path):
    """Um cache antigo não tem as chaves novas; ausência não é zero."""
    _write_cache(tmp_path)
    corpo = client.get("/api/status/meta").get_json()
    assert corpo["imported"] is None


# -- atualizar --------------------------------------------------------------


def test_atualizar_recusa_enquanto_outro_job_roda(client, monkeypatch):
    from web import jobs as jobs_module

    monkeypatch.setattr(
        jobs_module.JobManager, "busy", property(lambda self: True)
    )
    resposta = client.post("/api/status/refresh", json={})
    assert resposta.status_code == 409
    assert messages.UI_JOB_BUSY in resposta.get_json()["error"]


def test_atualizar_nunca_grava_nada(client, monkeypatch):
    """A varredura é somente leitura, e o job não recebe modo de gravação.

    Vale como trava: a aba fica ao lado de duas que gravam, e um --apply
    acidental aqui rodaria contra a instância inteira.
    """
    capturado = {}

    def _fake(self, verify=False):
        capturado["verify"] = verify
        capturado["assinatura"] = True

        class _Job:
            id = "job-teste"

        return _Job()

    from web import jobs as jobs_module

    monkeypatch.setattr(jobs_module.JobManager, "start_status", _fake, raising=False)
    resposta = client.post("/api/status/refresh", json={"verify": True})
    assert resposta.status_code == 200
    assert capturado["verify"] is True
    assert "apply" not in capturado


# -- o limite do lote -------------------------------------------------------


def test_o_lote_sugere_500_por_execucao(client):
    """Decisão operacional: as partidas são feitas de 500 em 500."""
    pagina = client.get("/").get_data(as_text=True)
    assert 'id="batch-limit"' in pagina
    assert 'value="500"' in pagina


def test_o_limite_continua_livre_acima_de_500(client, monkeypatch):
    """Sugestão, não trava: 500 é o padrão, não um teto no servidor."""
    from web import jobs as jobs_module

    chamado = {}

    def _fake(self, project, apply_mode, limit=None):
        chamado["limit"] = limit

        class _Job:
            id = "job-teste"

        return _Job()

    monkeypatch.setattr(jobs_module.JobManager, "start_batch", _fake)
    resposta = client.post(
        "/api/batch", json={"project": "hydro", "mode": "dry", "limit": 900}
    )
    assert resposta.status_code == 200
    assert chamado["limit"] == 900

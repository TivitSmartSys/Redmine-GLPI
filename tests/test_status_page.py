"""O painel de progresso: os números que ele afirma, e de onde eles vêm.

Três coisas são cobertas aqui, e as três já estiveram erradas na página que
foi publicada como artefato:

1. O denominador era uma constante transcrita. Medido em 2026-09-09, o Redmine
   tinha 5649 raízes contra as 5628 escritas no código - a HYDRO sozinha subiu
   de 171 para 183 em nove dias. Um "faltam N" calculado sobre a constante
   erra em silêncio e piora com o tempo.

2. A página dizia "Fase 0 concluída: 1262 projetos do import removidos". Isso
   foi verdade na instância de TESTE. Na produção os 789 projetos do import de
   2026-06-06 continuam lá, sem marcador nenhum - a frase era uma afirmação
   falsa sobre a instância que o painel está lendo.

3. Contar esses 789 pelo nome é uma armadilha: assuntos reais do Redmine
   contêm "RDM <n>" (RDM 18557 chama-se "RDM 18291 - Cascavel | 3 instalações
   ..."). Um projeto que ESTA migração criou não pode ser contado como import
   só porque o assunto cita outro chamado.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import migration_status as status  # noqa: E402


def _row(redmine_id: int, glpi_id: int, *, name: str = "Projeto", tracker=None) -> status.Row:
    return status.Row(
        redmine_id=redmine_id,
        glpi_id=glpi_id,
        name=name,
        entity="CEMIG D",
        created="2026-01-01 00:00:00",
        tasks=0,
        notes=0,
        documents=0,
        tracker=tracker,
    )


# -- o denominador -----------------------------------------------------------


def test_escopo_medido_substitui_a_constante():
    """O que foi lido do Redmine agora vale mais do que o que foi transcrito."""
    medido = {"hydro": ("tracker 42", 183), "operacao-cemig": ("tracker 39", 6)}
    totais = status.progress_totals([_row(1, 100)], scope=medido)
    assert totais["scope"] == 189
    assert totais["scope_rows"] == medido


def test_sem_medicao_cai_na_constante():
    """A constante continua existindo como último recurso, não some."""
    totais = status.progress_totals([_row(1, 100)])
    assert totais["scope"] == sum(n for _, n in status.SCOPE_ROOTS.values())
    assert totais["scope_rows"] == status.SCOPE_ROOTS


# -- o que NÃO é desta migração ---------------------------------------------


def test_projeto_desta_migracao_nao_conta_como_import():
    """Um assunto que cita "RDM <n>" não torna o projeto um import.

    O caso real: RDM 18557 → "RDM 18291 - Cascavel | 3 instalações de rádio".
    Ele tem marcador, foi criado por esta migração, e contá-lo entre os
    importados inflaria o número exatamente onde a página promete clareza.
    """
    projetos = [
        {"id": 1032, "name": "RDM 18291 - Cascavel | 3 instalações de rádio"},
        {"id": 900, "name": "RDM 16950"},
    ]
    assert status.count_imported(projetos, marked_hosts={1032}) == 1


def test_conta_so_os_nomes_com_numero_de_chamado():
    """"RDM" solto não é um chamado; "RDM 17018" é."""
    projetos = [
        {"id": 1, "name": "RDM 17018"},
        {"id": 2, "name": "rdm 4021 - alguma coisa"},
        {"id": 3, "name": "Manutenção Preventiva Ciclo 2024/2025"},
        {"id": 4, "name": "RDM sem número"},
    ]
    assert status.count_imported(projetos, marked_hosts=set()) == 2


def test_sem_projetos_o_numero_e_zero_e_nao_none():
    assert status.count_imported([], marked_hosts=set()) == 0


# -- a página ----------------------------------------------------------------


def test_pagina_declara_os_projetos_fora_desta_migracao():
    """O número medido aparece, e a frase falsa sobre a Fase 0 não."""
    totais = status.progress_totals([_row(1, 100)], imported=789)
    pagina = status.render_html([_row(1, 100)], totais)
    assert "789" in pagina
    assert "Fase 0 concluída" not in pagina


def test_pagina_sem_medicao_de_import_nao_inventa_zero():
    """Não medido é "?", nunca 0 - a mesma regra da coluna "sem mapa"."""
    totais = status.progress_totals([_row(1, 100)])
    assert totais["imported"] is None
    pagina = status.render_html([_row(1, 100)], totais)
    assert "não medido" in pagina


def test_legenda_usa_o_escopo_medido():
    """A barra por projeto do Redmine conta contra o total lido agora."""
    medido = {"operacao-cemig": ("tracker 39", 6)}
    linhas = [_row(18586 + i, 1033 + i, tracker=39) for i in range(6)]
    pagina = status.render_html(linhas, status.progress_totals(linhas, scope=medido))
    assert "6 / 6" in pagina


def test_progresso_e_calculado_sobre_o_escopo_medido():
    linhas = [_row(1, 100), _row(2, 101)]
    pagina = status.render_html(linhas, status.progress_totals(linhas, scope={"x": ("tracker 14", 200)}))
    assert "faltam 198" in pagina

"""Tabela de acompanhamento da migração: o que já está no GLPI, item a item.

    python migration_status.py                      # tabela no terminal
    python migration_status.py --out reports/STATUS.md
    python migration_status.py --verify             # confere contra o Redmine (lento)
    python migration_status.py --verify --limit 30  # confere só os 30 mais recentes

Somente leitura. Não escreve no GLPI nem no Redmine, em nenhum modo.

A tabela é GERADA, nunca escrita à mão. Contar estas linhas manualmente já
produziu dois erros no mesmo dia - um em cada direção - e a contagem manual
piora à medida que o lote cresce.

DUAS ARMADILHAS DE LEITURA, medidas em 2026-08-31, estão resolvidas aqui:

1. A rota plana `GET /ProjectTask` devolve ZERO linhas para esta instância
   inteira, mesmo com tarefas existindo e legíveis por id. Por isso as tarefas
   são contadas pelo `migration_map` local e conferidas por id, nunca listando
   o itemtype.
2. A seção 9 do relatório conta as notas da ÁRVORE INTEIRA. Comparar esse
   número com os diários só da issue raiz parece um déficit e não é. Aqui a
   contagem soma projeto + todas as suas tarefas, dos dois lados.

E daí uma terceira, que é a razão de existir a coluna "rastreável": como o GLPI
não lista ProjectTask por nenhuma rota, a única fonte de "quais tarefas são
deste projeto" é o `migration_map` local — que é uma proteção contra queda, NÃO
uma autoridade. Para um projeto migrado antes do banco atual o mapa está vazio,
e aí a contagem correta é DESCONHECIDA, nunca zero. Relatar zero ali produz uma
divergência inventada: foi exatamente o que a primeira versão desta tabela fez
com os projetos 1289 e 1290.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.errors import ApiError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402
from config.settings import (  # noqa: E402
    DEFAULT_DB_PATH,
    ITEMTYPE_ADDITIONAL_FIELDS,
    ConfigError,
    load_settings,
)
from report import messages  # noqa: E402
from templates_status_page import PAGE as _PAGE  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


@dataclass
class Row:
    """Uma linha da tabela: um projeto migrado."""

    redmine_id: int
    glpi_id: int
    name: str
    entity: str
    created: str
    tasks: int | None  # None = fora do mapa local, portanto desconhecido
    notes: int
    documents: int
    # Preenchidos apenas com --verify; None significa "não conferido".
    expected_tasks: int | None = None
    expected_notes: int | None = None
    expected_documents: int | None = None
    # Tracker da issue raiz; preenchido só com --verify. Serve para dizer a
    # qual projeto do Redmine esta raiz pertence, sem uma varredura extra.
    tracker: int | None = None

    @property
    def traceable(self) -> bool:
        """O mapa local conhece esta árvore? Se não, não há o que comparar."""
        return self.tasks is not None

    @property
    def verified(self) -> bool:
        return self.expected_tasks is not None and self.traceable

    @property
    def matches(self) -> bool:
        return (
            self.tasks == self.expected_tasks
            and self.notes == self.expected_notes
            and self.documents == self.expected_documents
        )

    @property
    def status(self) -> str:
        if not self.traceable:
            return "sem mapa"
        if not self.verified:
            return "—"
        return "OK" if self.matches else "DIVERGE"


def task_ids_for(conn: sqlite3.Connection, redmine_ids: list[int]) -> list[int]:
    """Tarefas GLPI de uma árvore, pelo mapa local.

    Pelo mapa e não por listagem: ver a armadilha 1 no topo do arquivo.
    """
    if not redmine_ids:
        return []
    placeholders = ",".join("?" * len(redmine_ids))
    rows = conn.execute(
        "SELECT glpi_id FROM migration_map "
        f"WHERE glpi_itemtype = 'ProjectTask' AND redmine_id IN ({placeholders})",
        redmine_ids,
    ).fetchall()
    return [int(r[0]) for r in rows]


def tree_redmine_ids(conn: sqlite3.Connection, root_id: int) -> list[int]:
    """Ids Redmine da árvore, do mapa local - sem tocar no Redmine."""
    rows = conn.execute(
        "SELECT DISTINCT redmine_id FROM migration_map "
        "WHERE redmine_id = ? OR parent_redmine_id = ?",
        (int(root_id), int(root_id)),
    ).fetchall()
    return [int(r[0]) for r in rows]


def collect(
    glpi: GlpiClient,
    conn: sqlite3.Connection,
    redmine: RedmineClient | None = None,
    limit: int | None = None,
) -> list[Row]:
    """Lê o estado atual. Somente GETs, dos dois lados."""
    glpi.set_active_entity_root()

    entities = {}
    try:
        entities = {v: k for k, v in glpi.load_entities().items()}
    except ApiError:
        pass  # nomes de entidade são enfeite; a tabela funciona sem eles

    marker_rows = glpi.iter_all_rows(ITEMTYPE_ADDITIONAL_FIELDS)
    pairs: list[tuple[int, int]] = []
    for row in marker_rows:
        marker = str(row.get("rdmfield") or "").strip()
        host = int(row.get("items_id") or 0)
        if marker.isdigit() and host:
            pairs.append((int(marker), host))
    pairs.sort(key=lambda p: p[1], reverse=True)
    if limit:
        pairs = pairs[:limit]

    out: list[Row] = []
    for redmine_id, glpi_id in pairs:
        project = glpi.get_item("Project", glpi_id)
        if project is None:
            continue

        # Sem linha de Project no mapa, a árvore é invisível para nós: o GLPI
        # não lista ProjectTask por rota nenhuma nesta instância.
        tracked = conn.execute(
            "SELECT 1 FROM migration_map WHERE redmine_id = ? AND glpi_itemtype = 'Project'",
            (redmine_id,),
        ).fetchone() is not None

        tree_ids = tree_redmine_ids(conn, redmine_id) or [redmine_id]
        task_ids = task_ids_for(conn, tree_ids) if tracked else []

        notes = len(glpi.notepad_rows("Project", glpi_id))
        documents = len(glpi.document_links("Project", glpi_id))
        for task_id in task_ids:
            notes += len(glpi.notepad_rows("ProjectTask", task_id))
            documents += len(glpi.document_links("ProjectTask", task_id))

        entity_id = int(project.get("entities_id") or 0)
        row = Row(
            redmine_id=redmine_id,
            glpi_id=glpi_id,
            name=str(project.get("name") or "").strip(),
            entity=entities.get(entity_id, str(entity_id)),
            created=str(project.get("date_creation") or "")[:19],
            tasks=len(task_ids) if tracked else None,
            notes=notes,
            documents=documents,
        )

        if redmine is not None:
            # O tracker é lido SEMPRE que há Redmine, mesmo sem mapa local:
            # ele diz a qual projeto do Redmine esta raiz pertence, e o painel
            # conta o progresso por projeto. Ler só nas linhas rastreáveis
            # subnotificava o progresso justamente nas linhas que já são as
            # mais opacas - 5 de 5451 em vez de 7, no primeiro teste.
            _fill_tracker(row, redmine)
            if tracked:
                _fill_expected(row, redmine)
        out.append(row)
    return out


def _fill_tracker(row: Row, redmine: RedmineClient) -> None:
    """A qual tracker (logo, a qual projeto do Redmine) esta raiz pertence."""
    try:
        issue = redmine.fetch_issue(row.redmine_id, include=())
    except ApiError:
        return
    row.tracker = (issue.get("tracker") or {}).get("id")


def _fill_expected(row: Row, redmine: RedmineClient) -> None:
    """O que o Redmine diz que deveria existir. Um GET por árvore."""
    try:
        tree = redmine.fetch_tree(row.redmine_id)
    except ApiError:
        return
    nodes = [node for node, _ in tree.root.walk()]
    row.expected_tasks = len(nodes) - 1
    row.expected_notes = sum(
        1
        for node in nodes
        for journal in (node.issue.get("journals") or [])
        if str(journal.get("notes") or "").strip()
    )
    row.expected_documents = sum(
        len(node.issue.get("attachments") or []) for node in nodes
    )


def render(rows: list[Row], verified: bool) -> str:
    """A tabela, em Markdown."""
    total_tasks = sum(r.tasks or 0 for r in rows)
    untracked = [r for r in rows if not r.traceable]
    total_notes = sum(r.notes for r in rows)
    total_docs = sum(r.documents for r in rows)

    lines = [
        "# Acompanhamento da migração Redmine → GLPI",
        "",
        f"Gerado em {datetime.now():%Y-%m-%d %H:%M:%S} por `migration_status.py`.",
        "Tabela gerada a partir do estado real do GLPI — não editar à mão.",
        "",
        f"**{len(rows)} projetos migrados** · {total_tasks} tarefas · "
        f"{total_notes} notas · {total_docs} documentos",
        "",
    ]

    if untracked:
        lines += [
            f"> {len(untracked)} projeto(s) estão fora do mapa local "
            "(`migration_map`) — migrados antes do banco atual. Para eles a "
            "contagem de tarefas é **desconhecida**, não zero: o GLPI desta "
            "instância não lista `ProjectTask` por rota nenhuma, então sem o "
            "mapa não há como saber quais tarefas pertencem ao projeto. "
            "Notas e arquivos mostrados são só os do próprio projeto.",
            "",
        ]

    if verified:
        divergent = [r for r in rows if r.verified and not r.matches]
        if divergent:
            lines += [
                f"> **{len(divergent)} projeto(s) divergem do Redmine** — ver a "
                "coluna Confere.",
                "",
            ]
        else:
            lines += [
                "> Conferido contra o Redmine: todos os projetos batem "
                "tarefa a tarefa, nota a nota, arquivo a arquivo.",
                "",
            ]
        header = (
            "| RDM | GLPI | Tarefas | Notas | Arquivos | Confere | Entidade | "
            "Criado em | Projeto |"
        )
        sep = "|---:|---:|---:|---:|---:|:---:|---|---|---|"
    else:
        header = "| RDM | GLPI | Tarefas | Notas | Arquivos | Entidade | Criado em | Projeto |"
        sep = "|---:|---:|---:|---:|---:|---|---|---|"

    lines += [header, sep]
    for r in rows:
        name = r.name[:60].replace("|", "\\|")
        if verified:
            tasks = _cell(r.tasks, r.expected_tasks)
            notes = _cell(r.notes, r.expected_notes)
            docs = _cell(r.documents, r.expected_documents)
            lines.append(
                f"| {r.redmine_id} | {r.glpi_id} | {tasks} | {notes} | {docs} | "
                f"{r.status} | {r.entity} | {r.created} | {name} |"
            )
        else:
            lines.append(
                f"| {r.redmine_id} | {r.glpi_id} | {_cell(r.tasks, None)} | "
                f"{r.notes} | {r.documents} | {r.entity} | {r.created} | {name} |"
            )
    lines.append("")
    return "\n".join(lines)


def _cell(actual: int | None, expected: int | None) -> str:
    """`7` quando bate, `5 / 7` quando não, `?` quando não dá para saber."""
    if actual is None:
        return "?"
    if expected is None or actual == expected:
        return str(actual)
    return f"**{actual} / {expected}**"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migration_status.py",
        description=(
            "Tabela de acompanhamento dos projetos já migrados. Somente leitura."
        ),
    )
    parser.add_argument("--out", default=None, help="Grava a tabela neste arquivo .md.")
    parser.add_argument(
        "--pending-files",
        default=None,
        help="Grava a lista de anexos recusados pelo GLPI neste arquivo .md.",
    )
    parser.add_argument(
        "--html",
        default=None,
        help="Grava o painel HTML neste arquivo (para publicar como página).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Confere cada árvore contra o Redmine. Lento: um GET por projeto.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Somente os N projetos mais recentes."
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=messages.CLI_HELP_DB)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(messages.CONFIG_MISSING_VARS.format(names=str(exc)), file=sys.stderr)
        return EXIT_CONFIG
    messages.register_secrets(settings.secret_values())

    # A lista de pendências nasce só dos relatórios em disco. Quando é a única
    # coisa pedida, não há motivo para abrir sessão com o GLPI nem ler o
    # Redmine: sai na hora e funciona offline.
    only_pending = args.pending_files and not (args.out or args.html or args.verify)
    if only_pending:
        pend = scan_pending_files()
        path = Path(args.pending_files)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_pending(pend), encoding="utf-8")
        print(f"Pendências salvas em {path} ({len(pend)} arquivo(s)).")
        return EXIT_OK

    conn = sqlite3.connect(args.db)
    try:
        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi:
            if args.verify:
                with RedmineClient(
                    settings.redmine_url, settings.redmine_api_key
                ) as redmine:
                    rows = collect(glpi, conn, redmine, limit=args.limit)
            else:
                rows = collect(glpi, conn, limit=args.limit)
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED
    finally:
        conn.close()

    if args.pending_files:
        pend = scan_pending_files()
        path = Path(args.pending_files)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_pending(pend), encoding='utf-8')
        print(f'Pendências salvas em {path} ({len(pend)} arquivo(s)).')

    if args.html:
        path = Path(args.html)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_html(rows, progress_totals(rows)), encoding="utf-8")
        print(f"Painel salvo em {path} ({len(rows)} projetos).")

    text = render(rows, verified=args.verify)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Tabela salva em {path} ({len(rows)} projetos).")
    else:
        print(text)
    return EXIT_OK




# -- painel HTML -----------------------------------------------------------
#
# A mesma tabela, como página publicável. Fica aqui e não num template solto
# para que a página nunca fique defasada em relação aos números: uma execução
# gera as duas saídas a partir da mesma leitura.

# Total de raízes em escopo por projeto do Redmine, medido em 2026-08-31.
# O Redmine é um sistema vivo e cresce: hydro foi de 170 para 171 em dois dias.
# Reconfira com `batch.selection.candidate_roots` antes de citar estes números
# fora daqui.
SCOPE_ROOTS = {
    "operacao-cemig": ("tracker 39", 6),
    "hydro": ("tracker 42", 171),
    "projetos-telecom": ("tracker 14", 5451),
}


def progress_totals(rows: list[Row]) -> dict:
    """Números do cabeçalho do painel, derivados das linhas lidas."""
    return {
        "projects": len(rows),
        "tasks": sum(r.tasks or 0 for r in rows),
        "notes": sum(r.notes for r in rows),
        "documents": sum(r.documents for r in rows),
        "diverging": sum(1 for r in rows if r.verified and not r.matches),
        "untracked": sum(1 for r in rows if not r.traceable),
        "scope": sum(n for _, n in SCOPE_ROOTS.values()),
    }


def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_cell(actual: int | None, expected: int | None) -> str:
    if actual is None:
        return '<td class="num qmark">?</td>'
    if expected is None or actual == expected:
        return f'<td class="num">{actual}</td>'
    return f'<td class="num mismatch">{actual} / {expected}</td>'


def render_html(rows: list[Row], totals: dict) -> str:
    body = []
    for r in rows:
        if not r.traceable:
            css, pill, label = "is-nomap", "nomap", "sem mapa"
        elif r.verified and not r.matches:
            css, pill, label = "is-diverge", "diverge", "DIVERGE"
        elif r.verified:
            css, pill, label = "is-ok", "ok", "OK"
        else:
            css, pill, label = "is-ok", "ok", "—"
        entity = _esc(r.entity.split(" > ")[-1].upper() if " > " in r.entity else r.entity)
        body.append(
            f'<tr class="{css}">'
            f'<td class="id stripe">{r.redmine_id}</td>'
            f'<td class="num">{r.glpi_id}</td>'
            f"{_html_cell(r.tasks, r.expected_tasks)}"
            f"{_html_cell(r.notes, r.expected_notes)}"
            f"{_html_cell(r.documents, r.expected_documents)}"
            f'<td><span class="pill {pill}">{label}</span></td>'
            f'<td class="ent">{entity}</td>'
            f'<td class="when">{_esc(r.created[:16])}</td>'
            f'<td class="name">{_esc(r.name[:70])}</td>'
            "</tr>"
        )

    # Quantas raízes de cada projeto do Redmine já estão migradas. Vem do
    # tracker da própria raiz, que --verify já leu - sem varredura extra.
    by_tracker: dict[int, int] = {}
    for r in rows:
        if r.tracker is not None:
            by_tracker[r.tracker] = by_tracker.get(r.tracker, 0) + 1

    legend = []
    for ident, (tracker_label, total) in SCOPE_ROOTS.items():
        tracker_id = int(tracker_label.split()[-1])
        n = by_tracker.get(tracker_id, 0)
        tracker = tracker_label
        legend.append(
            '<div class="leg">'
            f'<div class="leg-name"><span class="dot" style="background:var(--{"ok" if n>=total else "unknown"})"></span>'
            f"{_esc(ident)} · {tracker}</div>"
            f'<div class="leg-num">{n} / {total}</div></div>'
        )

    pct = totals["projects"] / totals["scope"] * 100 if totals["scope"] else 0
    stamp = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    return _PAGE.format(
        stamp=stamp,
        projects=totals["projects"],
        scope=totals["scope"],
        remaining=totals["scope"] - totals["projects"],
        pct=f"{pct:.2f}",
        tasks=totals["tasks"],
        notes=totals["notes"],
        documents=totals["documents"],
        diverging=totals["diverging"],
        untracked=totals["untracked"],
        rows="\n        ".join(body),
        legend="\n      ".join(legend),
    )




# -- pendências de upload manual -------------------------------------------
#
# Decisão do responsável, 2026-08-31: arquivos grandes demais para o
# `post_max_size` do PHP não bloqueiam o lote e serão enviados à mão depois,
# se forem necessários. Para isso é preciso saber QUAIS - e essa informação
# nasce espalhada por um relatório por projeto, o que a 5451 projetos não é
# consultável. Esta função a reúne numa lista de trabalho.
#
# A fonte são os relatórios já gravados em disco, não uma nova leitura do
# Redmine: o que interessa é o que a migração de fato tentou e não conseguiu.

_FAILED_LINE = re.compile(
    r"^\s+- (?P<name>.+?)\s+\((?P<size>[\d.,]+ [KMG]?B)\)\s+\[FALHA(?P<why>[^\]]*)\]",
)
_PROJECT_LINE = re.compile(r"^\s+(?:Projeto|Tarefa) RDM (?P<rdm>\d+)")


@dataclass
class Pending:
    redmine_id: int
    host_label: str
    filename: str
    size: str
    reason: str
    report: str


def scan_pending_files(reports_dir: str | Path = "reports") -> list[Pending]:
    """Todo anexo que a migração tentou enviar e o GLPI recusou."""
    found: list[Pending] = []
    for path in sorted(Path(reports_dir).glob("*/RDM*.txt")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        host_rdm, host_label = 0, ""
        for index, line in enumerate(lines):
            host = _PROJECT_LINE.match(line)
            if host:
                host_rdm = int(host.group("rdm"))
                host_label = line.strip()[:80]
            hit = _FAILED_LINE.match(line)
            if not hit:
                continue
            reason = ""
            for follow in lines[index + 1 : index + 3]:
                if "Erro" in follow or "ERROR" in follow:
                    reason = follow.strip()
                    break
            found.append(
                Pending(
                    redmine_id=host_rdm,
                    host_label=host_label,
                    filename=hit.group("name").strip(),
                    size=hit.group("size"),
                    reason=reason or hit.group("why").strip(),
                    report=str(path),
                )
            )
    return found


def render_pending(items: list[Pending]) -> str:
    lines = [
        "# Arquivos para envio manual",
        "",
        f"Gerado em {datetime.now():%Y-%m-%d %H:%M:%S} por "
        "`migration_status.py --pending-files`.",
        "",
        "Anexos que a migração tentou enviar e o GLPI recusou. Eles continuam "
        "no Redmine, intactos — a migração nunca escreve na origem. O projeto e "
        "as tarefas correspondentes já existem no GLPI; falta só o arquivo.",
        "",
    ]
    if not items:
        lines += ["Nenhuma pendência: todo anexo encontrado foi enviado.", ""]
        return "\n".join(lines)

    lines += [
        f"**{len(items)} arquivo(s) pendente(s).**",
        "",
        "| RDM | Arquivo | Tamanho | Motivo |",
        "|---:|---|---:|---|",
    ]
    for item in items:
        name = item.filename.replace("|", "\\|")
        reason = item.reason.replace("|", "\\|")[:90]
        lines.append(
            f"| {item.redmine_id} | {name} | {item.size} | {reason} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

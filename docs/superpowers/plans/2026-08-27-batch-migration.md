# Batch Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Purge the poisoned 2026-06-06 import from GLPI, then migrate all 5627 in-scope Redmine roots in supervised, resumable batches.

**Architecture:** Two independent CLIs beside the existing `main.py` and `reset_migration.py`. Phase 0 (`purge_import.py`) is a one-shot destructive cleanup that restores `rdmfield` as a trustworthy dedup authority. Phase 1 (`migrate_batch.py`) is a loop, an ordering and a ledger around the *existing* pipeline — it calls `build_project_plan` → `apply_plan` per item and duplicates no migration logic.

**Tech Stack:** Python 3, `requests`, `sqlite3`, `pytest`, `argparse`. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-08-27-batch-migration-design.md](../specs/2026-08-27-batch-migration-design.md)

## Global Constraints

- **The Redmine API is read-only. No phase ever writes to it.** Migration state lives in GLPI (`rdmfield`) and in the local SQLite ledger — never in the source.
- **Dry-run is the default** in both new CLIs. No write without `--apply` *and* a confirmation, per CLAUDE.md hard rule 4.
- **All user-facing text is PT-BR and lives in `report/messages.py`.** Code identifiers and comments are English. Migrated data is copied verbatim, never translated.
- **Exit codes:** `0` ok, `1` failed/aborted, `2` configuration error.
- **Secrets never appear in output.** Anything user-facing goes through `messages.redact()`.
- **Never auto-create a dropdown entry or an entity.** No match → skip the field and report it.
- **Nothing disappears silently** (spec 13, rule 7). Every skipped item, every failure and every purged project gets a report line.
- **Both CLIs must call `glpi.set_active_entity_root()` in preflight.** A session narrowed to entity 75 cannot see projects filed elsewhere, which silently breaks dedup rather than failing loudly.
- **Keep list (never purged):** `1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298`.
- **Purge target:** every project with `date_creation` on `2026-06-06`, plus test ids `1256, 1257, 1258, 1259, 1260, 1261, 1262, 1263, 1264, 1267, 1291, 1295`. Expected total 1265 of 1274.

---

## File Structure

| File | Responsibility |
|---|---|
| `purge_import.py` (create) | Phase 0 CLI: select targets, report, confirm, purge, verify |
| `clients/glpi.py` (modify) | Add `iter_all_rows()` — paged read of a whole itemtype |
| `store/batch.py` (create) | `BatchLedger` — the `batch_item` table, separate from `migration_map` |
| `batch/__init__.py` (create) | Package marker |
| `batch/selection.py` (create) | Build the pending list: roots minus GLPI markers |
| `batch/runner.py` (create) | The loop: one item = existing pipeline, failures isolated |
| `batch/report.py` (create) | `resumo.txt` aggregate, incorporating the Phase 0 record |
| `migrate_batch.py` (create) | Phase 1 CLI |
| `report/messages.py` (modify) | New PT-BR strings for both CLIs |
| `tests/test_readonly_redmine.py` (create) | Pins the read-only invariant |
| `tests/test_purge_import.py` (create) | Target selection, keep-list guard, order, verification |
| `tests/test_batch_ledger.py` (create) | Ledger states and resume |
| `tests/test_batch_selection.py` (create) | Pending list arithmetic |
| `tests/test_batch_runner.py` (create) | Failure isolation, session reuse |
| `tests/test_batch_report.py` (create) | Aggregate sums match the individual outcomes |

---

### Task 1: Pin the read-only Redmine invariant

The cheapest task and deliberately first: it makes the constraint executable before any batch code exists to violate it.

**Files:**
- Test: `tests/test_readonly_redmine.py`

**Interfaces:**
- Consumes: `clients.redmine.RedmineClient`
- Produces: nothing — a guard only

- [ ] **Step 1: Write the failing test**

```python
"""The Redmine API is read-only. This test exists so that stays true.

Closed decision 2026-08-27: the migration reads Redmine and never writes to it.
Migration state lives in GLPI (the rdmfield marker) and in the local ledger.
The batch phase is where this is most likely to be broken by accident - looping
over thousands of issues makes "mark it as migrated in Redmine" feel natural.
It is not allowed.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clients.redmine as redmine_module  # noqa: E402

WRITE_VERBS = ("post", "put", "delete", "patch")


def test_redmine_client_source_has_no_write_verb():
    source = inspect.getsource(redmine_module)
    for verb in WRITE_VERBS:
        pattern = rf"\.{verb}\s*\("
        assert not re.search(pattern, source), (
            f"clients/redmine.py calls .{verb}() - the Redmine API is read-only"
        )


def test_redmine_client_exposes_no_write_method():
    from clients.redmine import RedmineClient

    for name in dir(RedmineClient):
        assert not name.lower().startswith(WRITE_VERBS), (
            f"RedmineClient.{name} looks like a write method; Redmine is read-only"
        )
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_readonly_redmine.py -v`
Expected: PASS immediately — the invariant already holds. This test is a ratchet, not a red-green cycle.

- [ ] **Step 3: Prove the test can fail**

Temporarily add `self._session.post("/x")` inside `RedmineClient.close` in `clients/redmine.py`, re-run the test, confirm it FAILS with the `.post()` message, then **revert the edit**. A guard that cannot fail guards nothing.

Run: `python -m pytest tests/test_readonly_redmine.py -v`
Expected: FAIL, then PASS again after reverting.

- [ ] **Step 4: Commit**

```bash
git add tests/test_readonly_redmine.py
git commit -m "test: pin the read-only Redmine invariant"
```

---

### Task 2: Paged read of a whole itemtype

Both phases need every container-15 row. `_search(full_range=True)` caps at `SEARCH_FETCH_RANGE` = `0-999` and GLPI holds 930 rows today — close enough to the ceiling that a silent truncation is a live risk, and truncation here means the batch re-migrates whatever falls past the cap.

**Files:**
- Modify: `clients/glpi.py` (add a public method beside `_search`)
- Test: `tests/test_batch_selection.py`

**Interfaces:**
- Consumes: `GlpiClient._request`
- Produces: `GlpiClient.iter_all_rows(itemtype: str, page_size: int = 200) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
"""Reading a whole itemtype must not stop at a page boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.glpi import GlpiClient  # noqa: E402


def client_returning(pages):
    """A GlpiClient with no session, answering canned pages in order."""
    client = GlpiClient.__new__(GlpiClient)
    calls = []

    def fake_request(method, path, params=None, **kwargs):
        calls.append((path, (params or {}).get("range")))
        return pages.pop(0) if pages else []

    client._request = fake_request
    client.calls = calls
    return client


def test_iter_all_rows_follows_every_page():
    full = [{"id": n} for n in range(200)]
    tail = [{"id": 200 + n} for n in range(30)]
    client = client_returning([full, tail])

    rows = client.iter_all_rows("PluginFieldsProjectcamposadicionaisprojeto")

    assert len(rows) == 230
    assert client.calls[0][1] == "0-199"
    assert client.calls[1][1] == "200-399"


def test_iter_all_rows_stops_on_short_page():
    client = client_returning([[{"id": 1}, {"id": 2}]])

    rows = client.iter_all_rows("Project")

    assert len(rows) == 2
    assert len(client.calls) == 1


def test_iter_all_rows_returns_empty_when_itemtype_has_no_rows():
    client = client_returning([[]])

    assert client.iter_all_rows("Project") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_selection.py -v`
Expected: FAIL with `AttributeError: 'GlpiClient' object has no attribute 'iter_all_rows'`

- [ ] **Step 3: Write minimal implementation**

Add to `clients/glpi.py`, directly below `_search`:

```python
    def iter_all_rows(self, itemtype: str, page_size: int = 200) -> list[dict]:
        """Every row of an itemtype, following the pages to the end.

        TRAP: `_search(full_range=True)` pins `range` to SEARCH_FETCH_RANGE
        ("0-999"), which is a ceiling, not a page loop. GLPI held 930
        container-15 rows on 2026-08-27 - close enough that one more import
        would have truncated the read in silence. A truncated read here does not
        raise; it makes the batch believe the rows past the cap were never
        migrated, and migrate them a second time.
        """
        rows: list[dict] = []
        start = 0
        while True:
            try:
                page = self._request(
                    "GET",
                    f"/{itemtype}",
                    params={"range": f"{start}-{start + page_size - 1}"},
                )
            except GlpiError as exc:
                # Same convention as _search: GLPI answers 400/404 for an empty
                # result set in some versions.
                if "ERROR_GLPI_SEARCH" in str(exc) or "404" in str(exc):
                    break
                raise
            if not isinstance(page, list) or not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            start += page_size
        return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_selection.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add clients/glpi.py tests/test_batch_selection.py
git commit -m "feat: page through a whole itemtype instead of capping at 1000 rows"
```

---

### Task 3: Purge target selection and the keep-list guard

Pure functions, no network. This is where the single most expensive mistake — purging a project that should survive — is made impossible.

**Files:**
- Create: `purge_import.py`
- Test: `tests/test_purge_import.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `KEEP_PROJECT_IDS: frozenset[int]`
  - `TEST_PROJECT_IDS: frozenset[int]`
  - `IMPORT_DATE: str` (`"2026-06-06"`)
  - `select_targets(projects: list[dict]) -> list[int]`
  - `class KeepListViolation(Exception)`

- [ ] **Step 1: Write the failing test**

```python
"""Phase 0 selects its targets positively and refuses to touch the keep list.

The keep list is not arbitrary: each of those nine projects was verified on
2026-08-27 to carry an rdmfield pointing at a real in-scope Redmine root whose
subject matches the project name. Everything else with a 2026-06-06 creation
date is a casca from the poisoned bulk import.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from purge_import import (  # noqa: E402
    IMPORT_DATE,
    KEEP_PROJECT_IDS,
    KeepListViolation,
    select_targets,
)


def project(pid, created="2026-06-06 15:40:00"):
    return {"id": pid, "date_creation": created, "name": f"P{pid}"}


def test_selects_every_project_created_on_the_import_date():
    projects = [project(100), project(101), project(1286, "2026-08-12 14:54:23")]

    assert select_targets(projects) == [100, 101]


def test_selects_the_explicit_test_ids_whatever_their_date():
    projects = [project(1291, "2026-08-19 10:58:33"), project(1295, "2026-08-25 11:05:44")]

    assert select_targets(projects) == [1291, 1295]


def test_never_selects_a_keep_list_project():
    projects = [project(pid) for pid in sorted(KEEP_PROJECT_IDS)]

    with pytest.raises(KeepListViolation) as exc:
        select_targets(projects)

    assert "1286" in str(exc.value)


def test_keep_list_holds_exactly_the_nine_verified_projects():
    assert KEEP_PROJECT_IDS == frozenset(
        {1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298}
    )


def test_import_date_is_the_measured_one():
    assert IMPORT_DATE == "2026-06-06"


def test_a_project_with_no_creation_date_is_left_alone():
    assert select_targets([{"id": 500, "date_creation": None, "name": "?"}]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'purge_import'`

- [ ] **Step 3: Write minimal implementation**

Create `purge_import.py`:

```python
"""Phase 0 of the batch migration: remove the poisoned 2026-06-06 import.

Why this exists (measured 2026-08-27, see the design doc): GLPI holds 1274
projects, 1253 of them created on 2026-06-06 by a bulk import that is not this
tool. That import wrote `rdmfield` misaligned - 698 markers contradict the RDM
number in their own project's name, 5 agree. One marker sits on 13 unrelated
projects. A further 344 of those projects carry no container-15 row at all.

The consequence is that dedup is broken in both directions at once: ~660 issues
would be skipped because their marker sits on someone else's project, and the
1253 cascas would be migrated a second time because their true issue cannot be
found by marker. Repairing the markers was rejected - the cascas hold no tasks,
no notes and no documents, so repair would leave them indistinguishable from
complete migrations.

Dry-run by default, like every other writer in this repo.
"""

from __future__ import annotations

# Projects created on this date belong to the poisoned bulk import.
IMPORT_DATE = "2026-06-06"

# Throwaways from manual testing. Some postdate the import; 1263 is named
# "RDM 16467 - ..." and would read as a real migration if left behind.
TEST_PROJECT_IDS = frozenset(
    {1256, 1257, 1258, 1259, 1260, 1261, 1262, 1263, 1264, 1267, 1291, 1295}
)

# Verified individually on 2026-08-27: each carries an rdmfield pointing at a
# real in-scope root whose subject matches the project name.
#   1286 -> RDM 20438 (t14)   1287 -> 20472 (t14)   1288 -> 2101  (t14)
#   1289 -> RDM 20280 (t14)   1290 -> 19533 (t14)   1292 -> 19074 (t39)
#   1293 -> RDM 18729 (t39)   1296 -> 20556 (t14)   1298 -> 15815 (t14)
# 1298 reads date_creation 2023-09-20 because it is the test of the date-shift
# feature added 2026-08-27, not because it predates the migration.
KEEP_PROJECT_IDS = frozenset({1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298})


class KeepListViolation(Exception):
    """A project that must survive was selected for purging."""


def select_targets(projects: list[dict]) -> list[int]:
    """Ids to purge, chosen positively - never as "everything else".

    Raises KeepListViolation rather than silently dropping a protected id: if
    the rule ever selects one, the rule is wrong and the operator must see it
    before anything is deleted.
    """
    targets: list[int] = []
    for row in projects:
        pid = int(row.get("id") or 0)
        if not pid:
            continue
        created = str(row.get("date_creation") or "")[:10]
        if created == IMPORT_DATE or pid in TEST_PROJECT_IDS:
            targets.append(pid)

    protected = sorted(set(targets) & KEEP_PROJECT_IDS)
    if protected:
        raise KeepListViolation(
            "projetos protegidos entraram no alvo: "
            + ", ".join(str(p) for p in protected)
        )
    return targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add purge_import.py tests/test_purge_import.py
git commit -m "feat: purge target selection with a keep-list guard"
```

---

### Task 4: Phase 0 dry-run, report file and confirmation gate

**Files:**
- Modify: `purge_import.py`
- Modify: `report/messages.py`
- Test: `tests/test_purge_import.py`

**Interfaces:**
- Consumes: `select_targets`, `GlpiClient.iter_all_rows`
- Produces:
  - `PurgeTarget` dataclass: `project_id: int`, `name: str`, `entities_id: int`, `container_row_ids: list[int]`, `marker: str`
  - `build_purge_plan(glpi) -> list[PurgeTarget]`
  - `render_purge_report(targets: list[PurgeTarget], applied: bool) -> str`
  - `confirm_purge() -> bool`
  - `build_parser() -> argparse.ArgumentParser`

- [ ] **Step 1: Add the PT-BR strings**

Append to `report/messages.py`:

```python
# -- purge_import.py (fase 0 da migração em lote) --------------------------

CLI_HELP_PURGE = (
    "Remove o import em lote de 2026-06-06 e os projetos de teste do GLPI. "
    "Sem --apply apenas mostra o que seria removido."
)
CLI_HELP_PURGE_APPLY = "Executa a remoção. Exige confirmação."
CLI_HELP_PURGE_YES = "Confirma sem perguntar (para pipelines)."
CLI_HELP_PURGE_REPORT = "Caminho do arquivo de registro da limpeza."

PURGE_HEADER = "LIMPEZA DO IMPORT DE 2026-06-06"
PURGE_TARGET_COUNT = "Projetos selecionados para remoção: {count}"
PURGE_KEPT_COUNT = "Projetos preservados: {count}"
PURGE_TARGET_LINE = "  {project_id:>6} | ent {entity:>3} | marcador {marker:<12} | {name}"
PURGE_KEEP_VIOLATION = (
    "ABORTADO: {detail}. Nenhum projeto foi removido. "
    "Corrija a regra de seleção antes de tentar de novo."
)
PURGE_CONFIRM_PROMPT = (
    "Isto remove {count} projetos do GLPI de forma DEFINITIVA (force_purge). "
    "Digite 'sim' para continuar: "
)
PURGE_CANCELLED = "Limpeza cancelada. Nada foi removido."
PURGE_NOTHING_TO_DO = "Nada a remover: o alvo está vazio."
PURGE_ROW_DELETED = "  removido {itemtype} {row_id} (projeto {project_id})"
PURGE_PROJECT_DELETED = "  removido Project {project_id}"
PURGE_ITEM_FAILED = (
    "  FALHA em {itemtype} {row_id} do projeto {project_id}: {detail}"
)
PURGE_SUMMARY = (
    "Removidos {projects} projetos, {containers} linhas de container, "
    "{tasks} tarefas, {notes} notas, {links} vínculos de documento. Falhas: {failed}."
)
PURGE_VERIFY_OK = (
    "Verificação: restam {projects} projetos e {containers} linhas de container 15, "
    "todas pertencentes aos projetos preservados."
)
PURGE_VERIFY_FAILED = (
    "Verificação FALHOU: restam {projects} projetos e {containers} linhas de "
    "container 15. Esperado {expected_projects} e somente linhas preservadas."
)
PURGE_REPORT_SAVED = "Registro da limpeza salvo em {path}"
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_purge_import.py`:

```python
from config.settings import ITEMTYPE_ADDITIONAL_FIELDS as CONTAINER  # noqa: E402
from purge_import import (  # noqa: E402
    PurgeTarget,
    build_parser,
    build_purge_plan,
    render_purge_report,
)


class FakeGlpi:
    """Answers iter_all_rows for Project and the container-15 itemtype."""

    def __init__(self, projects, container_rows):
        self._data = {"Project": projects, CONTAINER: container_rows}
        self.widened = False

    def set_active_entity_root(self):
        self.widened = True

    def iter_all_rows(self, itemtype, page_size=200):
        return list(self._data.get(itemtype, []))


def test_plan_pairs_each_target_with_its_container_rows():
    glpi = FakeGlpi(
        projects=[project(100), project(1286, "2026-08-12 14:54:23")],
        container_rows=[
            {"id": 7, "items_id": 100, "rdmfield": "17343"},
            {"id": 8, "items_id": 100, "rdmfield": ""},
            {"id": 9, "items_id": 1286, "rdmfield": "20438"},
        ],
    )

    plan = build_purge_plan(glpi)

    assert [t.project_id for t in plan] == [100]
    assert plan[0].container_row_ids == [7, 8]
    assert plan[0].marker == "17343"


def test_plan_widens_the_entity_session_before_reading():
    glpi = FakeGlpi(projects=[project(100)], container_rows=[])

    build_purge_plan(glpi)

    assert glpi.widened, "a narrowed session cannot see projects in other entities"


def test_report_names_every_target_and_the_kept_count():
    targets = [PurgeTarget(100, "Casca", 0, [7], "17343")]

    text = render_purge_report(targets, applied=False)

    assert "100" in text and "Casca" in text and "17343" in text
    assert "1" in text


def test_dry_run_is_the_default():
    assert build_parser().parse_args([]).apply is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: FAIL with `ImportError: cannot import name 'PurgeTarget'`

- [ ] **Step 4: Write minimal implementation**

Add to `purge_import.py`:

```python
import argparse
from dataclasses import dataclass, field

from config.settings import ITEMTYPE_ADDITIONAL_FIELDS
from report import messages


@dataclass
class PurgeTarget:
    project_id: int
    name: str
    entities_id: int
    container_row_ids: list[int] = field(default_factory=list)
    marker: str = ""


def build_purge_plan(glpi) -> list[PurgeTarget]:
    """Read-only. Pairs every target project with its container-15 rows.

    The entity session is widened first for the same reason preflight does it:
    a session active in entity 75 sees three entities, so most of the import
    would simply be invisible and survive the "purge" unmentioned.
    """
    glpi.set_active_entity_root()
    projects = glpi.iter_all_rows("Project")
    target_ids = select_targets(projects)
    wanted = set(target_ids)

    rows_by_project: dict[int, list[dict]] = {}
    for row in glpi.iter_all_rows(ITEMTYPE_ADDITIONAL_FIELDS):
        host = int(row.get("items_id") or 0)
        if host in wanted:
            rows_by_project.setdefault(host, []).append(row)

    by_id = {int(p.get("id") or 0): p for p in projects}
    plan: list[PurgeTarget] = []
    for pid in target_ids:
        source = by_id.get(pid, {})
        rows = rows_by_project.get(pid, [])
        marker = next(
            (str(r.get("rdmfield") or "").strip() for r in rows
             if str(r.get("rdmfield") or "").strip()),
            "",
        )
        plan.append(
            PurgeTarget(
                project_id=pid,
                name=str(source.get("name") or ""),
                entities_id=int(source.get("entities_id") or 0),
                container_row_ids=[int(r["id"]) for r in rows],
                marker=marker,
            )
        )
    return plan


def render_purge_report(targets: list[PurgeTarget], applied: bool) -> str:
    lines = [messages.PURGE_HEADER, "=" * len(messages.PURGE_HEADER), ""]
    lines.append(messages.PURGE_TARGET_COUNT.format(count=len(targets)))
    lines.append(messages.PURGE_KEPT_COUNT.format(count=len(KEEP_PROJECT_IDS)))
    lines.append("")
    for target in targets:
        lines.append(
            messages.PURGE_TARGET_LINE.format(
                project_id=target.project_id,
                entity=target.entities_id,
                marker=target.marker or "-",
                name=target.name[:60],
            )
        )
    return "\n".join(lines)


def confirm_purge(count: int) -> bool:
    try:
        answer = input(messages.PURGE_CONFIRM_PROMPT.format(count=count))
    except EOFError:
        return False
    return answer.strip().casefold() in messages.APPLY_CONFIRM_ACCEPT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="purge_import.py", description=messages.CLI_HELP_PURGE)
    parser.add_argument("--apply", action="store_true", help=messages.CLI_HELP_PURGE_APPLY)
    parser.add_argument("--yes", action="store_true", help=messages.CLI_HELP_PURGE_YES)
    parser.add_argument("--report", default=None, help=messages.CLI_HELP_PURGE_REPORT)
    return parser
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add purge_import.py report/messages.py tests/test_purge_import.py
git commit -m "feat: phase 0 dry-run plan, report and confirmation gate"
```

---

### Task 5: Phase 0 execution, ordered and verified

**Files:**
- Modify: `purge_import.py`
- Test: `tests/test_purge_import.py`

**Interfaces:**
- Consumes: `PurgeTarget`, `GlpiClient.delete_item`, `GlpiClient.notepad_rows`, `GlpiClient.document_links`, `GlpiClient.get_container_rows`
- Produces:
  - `PurgeCounts` dataclass: `projects`, `containers`, `tasks`, `notes`, `links`, `failed` (all `int`)
  - `purge_one(glpi, target) -> PurgeCounts`
  - `verify_purge(glpi) -> tuple[int, int]` returning `(project_count, container_row_count)`
  - `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_purge_import.py`:

```python
from purge_import import PurgeCounts, purge_one, verify_purge  # noqa: E402


class RecordingGlpi(FakeGlpi):
    def __init__(self, projects=(), container_rows=(), fail_on=None):
        super().__init__(list(projects), list(container_rows))
        self.deleted = []
        self.fail_on = fail_on or set()

    def delete_item(self, itemtype, item_id, force_purge=True):
        if (itemtype, item_id) in self.fail_on:
            from clients.errors import GlpiError

            raise GlpiError("recusado")
        assert force_purge is True, "phase 0 purges; the trash keeps the marker alive"
        self.deleted.append((itemtype, item_id))

    def notepad_rows(self, itemtype, items_id):
        return [{"id": 55}]

    def document_links(self, itemtype, items_id):
        return [{"id": 66}]

    def get_container_rows(self, itemtype, items_id):
        return []


def test_container_row_is_deleted_before_its_project():
    glpi = RecordingGlpi()
    target = PurgeTarget(100, "Casca", 0, [7], "17343")

    purge_one(glpi, target)

    itemtypes = [d[0] for d in glpi.deleted]
    assert itemtypes.index(CONTAINER) < itemtypes.index("Project"), (
        "deleting the project first leaves an orphan marker - the exact poison "
        "this phase removes"
    )


def test_purge_one_counts_everything_it_removed():
    glpi = RecordingGlpi()

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7, 8], "17343"))

    assert counts.projects == 1
    assert counts.containers == 2
    assert counts.notes == 1
    assert counts.links == 1
    assert counts.failed == 0


def test_a_failed_container_delete_does_not_delete_the_project():
    glpi = RecordingGlpi(fail_on={(CONTAINER, 7)})

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert counts.failed == 1
    assert ("Project", 100) not in glpi.deleted, (
        "the project must survive so the operator can retry; the reverse order "
        "would strand the marker"
    )


def test_verify_counts_what_survived():
    glpi = FakeGlpi(
        projects=[project(1286, "2026-08-12 14:54:23")],
        container_rows=[{"id": 9, "items_id": 1286, "rdmfield": "20438"}],
    )

    assert verify_purge(glpi) == (1, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: FAIL with `ImportError: cannot import name 'PurgeCounts'`

- [ ] **Step 3: Write minimal implementation**

Add to `purge_import.py`:

```python
from clients.errors import ApiError


@dataclass
class PurgeCounts:
    projects: int = 0
    containers: int = 0
    tasks: int = 0
    notes: int = 0
    links: int = 0
    failed: int = 0

    def add(self, other: "PurgeCounts") -> None:
        self.projects += other.projects
        self.containers += other.containers
        self.tasks += other.tasks
        self.notes += other.notes
        self.links += other.links
        self.failed += other.failed


def purge_one(glpi, target: PurgeTarget) -> PurgeCounts:
    """Remove one project and everything hanging off it.

    ORDER IS LOAD-BEARING AND INVERTED FROM THE OBVIOUS ONE. The container-15
    row goes first, the project last. A plugin container row outlives its host
    project (this is why reset_migration.py exists), so a failure after the
    project is gone strands the marker - the very poison being removed. Failing
    the other way round leaves a project with no marker, which is recoverable.
    """
    counts = PurgeCounts()

    def drop(itemtype: str, row_id: int) -> bool:
        try:
            glpi.delete_item(itemtype, row_id, force_purge=True)
        except ApiError as exc:
            print(
                messages.PURGE_ITEM_FAILED.format(
                    itemtype=itemtype, row_id=row_id,
                    project_id=target.project_id, detail=messages.redact(exc),
                ),
                file=sys.stderr,
            )
            counts.failed += 1
            return False
        print(
            messages.PURGE_ROW_DELETED.format(
                itemtype=itemtype, row_id=row_id, project_id=target.project_id
            )
        )
        return True

    for row_id in target.container_row_ids:
        if not drop(ITEMTYPE_ADDITIONAL_FIELDS, row_id):
            # Stop before the project: leaving a live project with a stranded
            # marker is strictly worse than leaving both in place.
            return counts
        counts.containers += 1

    for note in glpi.notepad_rows("Project", target.project_id):
        if drop("Notepad", int(note["id"])):
            counts.notes += 1

    for link in glpi.document_links("Project", target.project_id):
        if drop("Document_Item", int(link["id"])):
            counts.links += 1

    if drop("Project", target.project_id):
        counts.projects += 1
    return counts


def verify_purge(glpi) -> tuple[int, int]:
    """Re-read the two counts that prove the cleanup worked.

    The purge is not finished without this: it is the read that proves dedup is
    no longer poisoned. Returns (projects remaining, container-15 rows
    remaining).
    """
    projects = glpi.iter_all_rows("Project")
    containers = glpi.iter_all_rows(ITEMTYPE_ADDITIONAL_FIELDS)
    return len(projects), len(containers)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_purge_import.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Wire up `main()`**

Add to `purge_import.py`, following the `reset_migration.py` shape — load settings, register secrets, open `GlpiClient`, build the plan, print the report, gate on `--apply` plus confirmation, purge, verify, save the report file:

```python
import sys
from pathlib import Path

from clients.glpi import GlpiClient
from config.settings import ConfigError, load_settings

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


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

    try:
        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi:
            try:
                targets = build_purge_plan(glpi)
            except KeepListViolation as exc:
                print(messages.PURGE_KEEP_VIOLATION.format(detail=exc), file=sys.stderr)
                return EXIT_FAILED

            report = render_purge_report(targets, applied=False)
            print(report)

            if not targets:
                print(messages.PURGE_NOTHING_TO_DO)
                return EXIT_OK
            if not args.apply:
                return EXIT_OK
            if not (args.yes or confirm_purge(len(targets))):
                print(messages.PURGE_CANCELLED)
                return EXIT_OK

            totals = PurgeCounts()
            for target in targets:
                totals.add(purge_one(glpi, target))

            print()
            print(messages.PURGE_SUMMARY.format(
                projects=totals.projects, containers=totals.containers,
                tasks=totals.tasks, notes=totals.notes,
                links=totals.links, failed=totals.failed,
            ))

            projects_left, containers_left = verify_purge(glpi)
            expected = len(KEEP_PROJECT_IDS)
            ok = projects_left == expected
            print(
                (messages.PURGE_VERIFY_OK if ok else messages.PURGE_VERIFY_FAILED).format(
                    projects=projects_left, containers=containers_left,
                    expected_projects=expected,
                )
            )

            path = Path(args.report or "reports/purge-record.txt")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render_purge_report(targets, applied=True), encoding="utf-8"
            )
            print(messages.PURGE_REPORT_SAVED.format(path=path))
            return EXIT_OK if ok else EXIT_FAILED
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: PASS, 137 existing + the new ones

- [ ] **Step 7: Commit**

```bash
git add purge_import.py tests/test_purge_import.py
git commit -m "feat: phase 0 purge execution with inverted delete order and verification"
```

- [ ] **Step 8: Real dry-run against the live instance**

Run: `python purge_import.py`
Expected: 1265 targets listed, 9 kept, nothing written. **Read the list before going further.** Do not run `--apply` without the operator's explicit go-ahead — this is the destructive step and the plan stops here for a human decision.

---

### Task 6: The batch ledger

**Files:**
- Create: `store/batch.py`
- Test: `tests/test_batch_ledger.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class BatchLedger` with `__enter__`/`__exit__`, and:
    - `start_run(label: str) -> str` (returns run id)
    - `queue(run_id: str, issue_ids: list[int]) -> None`
    - `mark(run_id: str, issue_id: int, state: str, detail: str = "") -> None`
    - `pending(run_id: str) -> list[int]`
    - `counts(run_id: str) -> dict[str, int]`
    - `failures(run_id: str) -> list[tuple[int, str]]`
  - Constants `STATE_PENDING`, `STATE_OK`, `STATE_FAILED`, `STATE_SKIPPED`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'store.batch'`

- [ ] **Step 3: Write minimal implementation**

Create `store/batch.py`:

```python
"""The batch ledger: what a batch run has attempted, and how it went.

Deliberately a separate table from `migration_map`. That one answers "which
GLPI item does this Redmine id map to" and is consulted by apply_plan on every
node. This one answers "have we already tried this root in this run, and what
happened" - retry bookkeeping, which would corrupt the meaning of the other.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_item (
  run_id     TEXT    NOT NULL,
  issue_id   INTEGER NOT NULL,
  state      TEXT    NOT NULL,   -- 'pending' | 'ok' | 'failed' | 'skipped'
  detail     TEXT    NOT NULL DEFAULT '',
  queued_at  TEXT    NOT NULL,
  updated_at TEXT    NOT NULL,
  position   INTEGER NOT NULL,
  PRIMARY KEY (run_id, issue_id)
);
CREATE TABLE IF NOT EXISTS batch_run (
  run_id     TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  started_at TEXT NOT NULL
);
"""

STATE_PENDING = "pending"
STATE_OK = "ok"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"

# A failure is retried on --resume; a skip is not. A skip means the issue was
# already migrated, which no amount of retrying changes.
RETRYABLE = (STATE_PENDING, STATE_FAILED)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BatchLedger:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def start_run(self, label: str) -> str:
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self._conn.execute(
            "INSERT INTO batch_run (run_id, label, started_at) VALUES (?, ?, ?)",
            (run_id, label, _now()),
        )
        self._conn.commit()
        return run_id

    def queue(self, run_id: str, issue_ids: list[int]) -> None:
        now = _now()
        self._conn.executemany(
            """
            INSERT INTO batch_item
                (run_id, issue_id, state, detail, queued_at, updated_at, position)
            VALUES (?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(run_id, issue_id) DO NOTHING
            """,
            [
                (run_id, int(issue_id), STATE_PENDING, now, now, position)
                for position, issue_id in enumerate(issue_ids)
            ],
        )
        self._conn.commit()

    def mark(self, run_id: str, issue_id: int, state: str, detail: str = "") -> None:
        self._conn.execute(
            "UPDATE batch_item SET state = ?, detail = ?, updated_at = ? "
            "WHERE run_id = ? AND issue_id = ?",
            (state, detail, _now(), run_id, int(issue_id)),
        )
        self._conn.commit()

    def pending(self, run_id: str) -> list[int]:
        """Never-tried first, then failures. A retry must not delay fresh work."""
        rows = self._conn.execute(
            """
            SELECT issue_id FROM batch_item
            WHERE run_id = ? AND state IN (?, ?)
            ORDER BY CASE state WHEN ? THEN 0 ELSE 1 END, position
            """,
            (run_id, STATE_PENDING, STATE_FAILED, STATE_PENDING),
        ).fetchall()
        return [int(row["issue_id"]) for row in rows]

    def counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM batch_item WHERE run_id = ? GROUP BY state",
            (run_id,),
        ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    def failures(self, run_id: str) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT issue_id, detail FROM batch_item "
            "WHERE run_id = ? AND state = ? ORDER BY position",
            (run_id, STATE_FAILED),
        ).fetchall()
        return [(int(row["issue_id"]), str(row["detail"])) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "BatchLedger":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_ledger.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add store/batch.py tests/test_batch_ledger.py
git commit -m "feat: batch ledger with resume semantics"
```

---

### Task 7: Build the pending list

**Files:**
- Create: `batch/__init__.py`, `batch/selection.py`
- Test: `tests/test_batch_selection.py`

**Interfaces:**
- Consumes: `GlpiClient.iter_all_rows`, `RedmineClient.iter_issues`, `config.settings.IN_SCOPE_ROOT_TRACKERS`
- Produces:
  - `REDMINE_PROJECTS: dict[str, int]` — identifier → tracker id
  - `migrated_markers(glpi) -> set[int]`
  - `candidate_roots(redmine, tracker_id: int) -> list[int]`
  - `pending_roots(glpi, redmine, tracker_id: int, limit: int | None = None) -> list[int]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_batch_selection.py`:

```python
from batch.selection import (  # noqa: E402
    REDMINE_PROJECTS,
    candidate_roots,
    migrated_markers,
    pending_roots,
)


class MarkerGlpi:
    def __init__(self, rows):
        self._rows = rows

    def iter_all_rows(self, itemtype, page_size=200):
        return list(self._rows)


class FakeRedmine:
    def __init__(self, issues):
        self._issues = issues

    def iter_issues(self, tracker_id, page_size=100):
        for issue in self._issues:
            if issue["tracker"]["id"] == tracker_id:
                yield issue


def issue(iid, tracker=14, parent=None):
    row = {"id": iid, "tracker": {"id": tracker}}
    if parent:
        row["parent"] = {"id": parent}
    return row


def test_markers_are_read_in_one_bulk_pass_and_only_numeric_ones_count():
    glpi = MarkerGlpi([
        {"items_id": 100, "rdmfield": "17343"},
        {"items_id": 101, "rdmfield": "RDM123"},
        {"items_id": 102, "rdmfield": ""},
        {"items_id": 103, "rdmfield": "  20438  "},
    ])

    assert migrated_markers(glpi) == {17343, 20438}


def test_candidates_are_parentless_roots_of_that_tracker_only():
    redmine = FakeRedmine([
        issue(1), issue(2, parent=1), issue(3), issue(4, tracker=42),
    ])

    assert candidate_roots(redmine, 14) == [1, 3]


def test_pending_subtracts_the_markers():
    glpi = MarkerGlpi([{"items_id": 100, "rdmfield": "3"}])
    redmine = FakeRedmine([issue(1), issue(2), issue(3)])

    assert pending_roots(glpi, redmine, 14) == [1, 2]


def test_limit_takes_the_first_n_only():
    glpi = MarkerGlpi([])
    redmine = FakeRedmine([issue(1), issue(2), issue(3)])

    assert pending_roots(glpi, redmine, 14, limit=2) == [1, 2]


def test_the_three_redmine_projects_map_to_their_root_trackers():
    assert REDMINE_PROJECTS == {
        "projetos-telecom": 14,
        "hydro": 42,
        "operacao-cemig": 39,
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_selection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch'`

- [ ] **Step 3: Write minimal implementation**

Create `batch/__init__.py` (empty file), then `batch/selection.py`:

```python
"""Which roots still need migrating.

Measured 2026-08-27: Redmine has exactly four projects, and every in-scope root
is parentless. Three projects hold roots; "Configuração REDE CORP VOIP" holds no
issues at all, of any tracker, so it has no entry here.
"""

from __future__ import annotations

from config.settings import ITEMTYPE_ADDITIONAL_FIELDS

# Redmine project identifier -> the root tracker whose issues live in it.
# Each project holds exactly one root tracker; the asymmetry is measured, not
# assumed (tracker 39 is a root tracker only, tracker 40 a task tracker only).
REDMINE_PROJECTS = {
    "projetos-telecom": 14,   # Área de Telecom  - 5451 roots
    "hydro": 42,              # HYDRO            -  170 roots
    "operacao-cemig": 39,     # Operação CEMIG   -    6 roots
}


def migrated_markers(glpi) -> set[int]:
    """Every Redmine id GLPI already claims, from ONE bulk read.

    One read, not one search per root: at 5627 roots the per-issue path is the
    difference between a minute and an hour before any work starts. Non-numeric
    markers are ignored - the poisoned import left values like 'RDM123' and an
    ISO timestamp behind.
    """
    found: set[int] = set()
    for row in glpi.iter_all_rows(ITEMTYPE_ADDITIONAL_FIELDS):
        value = str(row.get("rdmfield") or "").strip()
        if value.isdigit():
            found.add(int(value))
    return found


def candidate_roots(redmine, tracker_id: int) -> list[int]:
    """Parentless issues of one tracker, in Redmine's own order."""
    return [
        int(issue["id"])
        for issue in redmine.iter_issues(tracker_id)
        if not issue.get("parent")
    ]


def pending_roots(glpi, redmine, tracker_id: int, limit: int | None = None) -> list[int]:
    already = migrated_markers(glpi)
    pending = [rid for rid in candidate_roots(redmine, tracker_id) if rid not in already]
    return pending[:limit] if limit else pending
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_selection.py -v`
Expected: PASS (8 tests — 3 from Task 2, 5 new)

- [ ] **Step 5: Commit**

```bash
git add batch/__init__.py batch/selection.py tests/test_batch_selection.py
git commit -m "feat: build the pending root list from one bulk marker read"
```

---

### Task 8: The runner — one item is the existing pipeline

**Files:**
- Create: `batch/runner.py`
- Test: `tests/test_batch_runner.py`

**Interfaces:**
- Consumes: `main.build_project_plan`, `main.apply_plan`, `main.check_already_migrated`, `BatchLedger`, `report.reporter.Reporter`
- Produces:
  - `run_batch(glpi, redmine, mapping, ledger, run_id, issue_ids, *, apply_mode, report_dir, skip_attachments=False, skip_notes=False) -> None`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.runner'`

- [ ] **Step 3: Write minimal implementation**

Create `batch/runner.py`:

```python
"""The batch loop: ordering, bookkeeping and failure isolation.

This module deliberately contains NO migration logic. One item is exactly the
existing `main.py` pipeline - build_project_plan then apply_plan - so every
behaviour recorded in CLAUDE.md (entities, containers, notes, attachments,
creation dates, the VARCHAR(255) truncation) applies unchanged and is not
re-implemented here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clients.errors import ApiError
from main import apply_plan, build_project_plan, check_already_migrated
from report import messages
from report.reporter import Reporter
from store.batch import STATE_FAILED, STATE_OK, STATE_SKIPPED
from store.db import MigrationStore


def render_item_report(plan, apply_mode: bool) -> str:
    return Reporter(plan, apply_mode=apply_mode).render()


def run_batch(
    glpi,
    redmine,
    mapping: dict,
    ledger,
    run_id: str,
    issue_ids: list[int],
    *,
    apply_mode: bool,
    report_dir,
    db_path: str = "migration.db",
    skip_attachments: bool = False,
    skip_notes: bool = False,
) -> None:
    """Migrate each root in turn. A failure is recorded, never fatal.

    Only two things end a run early: a dead GLPI session and a KeyboardInterrupt.
    Everything else - a missing entity, an oversized attachment, a 403 on one
    Redmine issue - belongs to its item and is written to the ledger.
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    total = len(issue_ids)

    store = MigrationStore(db_path) if apply_mode else None
    try:
        for position, issue_id in enumerate(issue_ids, start=1):
            print(messages.BATCH_ITEM_START.format(
                position=position, total=total, issue_id=issue_id
            ))
            try:
                existing = check_already_migrated(glpi, issue_id)
                if existing:
                    ledger.mark(
                        run_id, issue_id, STATE_SKIPPED,
                        messages.BATCH_ITEM_ALREADY.format(glpi_id=existing),
                    )
                    continue

                plan = build_project_plan(
                    glpi, redmine, mapping, issue_id,
                    skip_attachments=skip_attachments, skip_notes=skip_notes,
                )
                if apply_mode:
                    apply_plan(glpi, plan, store, redmine=redmine)

                (report_dir / f"RDM{issue_id}.txt").write_text(
                    render_item_report(plan, apply_mode), encoding="utf-8"
                )
                ledger.mark(run_id, issue_id, STATE_OK)
            except KeyboardInterrupt:
                # Leave the item pending so --resume picks it up unchanged.
                raise
            except Exception as exc:  # noqa: BLE001 - one item must not end the run
                detail = messages.redact(exc)
                print(
                    messages.BATCH_ITEM_FAILED.format(issue_id=issue_id, detail=detail),
                    file=sys.stderr,
                )
                ledger.mark(run_id, issue_id, STATE_FAILED, str(detail)[:500])
    finally:
        if store is not None:
            store.close()
```

Add to `report/messages.py`:

```python
# -- migrate_batch.py (fase 1) ---------------------------------------------

BATCH_ITEM_START = "[{position}/{total}] RDM {issue_id}"
BATCH_ITEM_ALREADY = "já migrado no GLPI (projeto {glpi_id})"
BATCH_ITEM_FAILED = "  FALHA em RDM {issue_id}: {detail}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_runner.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add batch/runner.py report/messages.py tests/test_batch_runner.py
git commit -m "feat: batch runner with per-item failure isolation"
```

---

### Task 9: The aggregate report

Requirement 7 of the spec: everything must be recorded in the final report, including what Phase 0 removed.

**Files:**
- Create: `batch/report.py`
- Test: `tests/test_batch_report.py`

**Interfaces:**
- Consumes: `BatchLedger.counts`, `BatchLedger.failures`
- Produces: `render_summary(ledger, run_id, label, purge_record: str | None = None) -> str`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batch.report'`

- [ ] **Step 3: Write minimal implementation**

Create `batch/report.py`:

```python
"""The run-level report. Sums the items; never replaces their own reports.

Spec 13 rule 7 says nothing disappears silently. The per-item reports prove no
FIELD vanished. This one proves no ITEM vanished: the four state counts add up
to the number queued, and every failure is printed with its reason rather than
tallied.
"""

from __future__ import annotations

from report import messages
from store.batch import STATE_FAILED, STATE_OK, STATE_PENDING, STATE_SKIPPED

_ORDER = (STATE_OK, STATE_SKIPPED, STATE_FAILED, STATE_PENDING)


def render_summary(ledger, run_id: str, label: str, purge_record: str | None = None) -> str:
    counts = ledger.counts(run_id)
    total = sum(counts.values())

    lines = [
        messages.BATCH_SUMMARY_HEADER.format(label=label, run_id=run_id),
        "=" * 60,
        "",
        messages.BATCH_SUMMARY_TOTAL.format(total=total),
    ]
    for state in _ORDER:
        lines.append(
            messages.BATCH_SUMMARY_STATE.format(state=state, count=counts.get(state, 0))
        )

    failures = ledger.failures(run_id)
    lines.append("")
    if failures:
        lines.append(messages.BATCH_SUMMARY_FAILURES.format(count=len(failures)))
        for issue_id, detail in failures:
            lines.append(
                messages.BATCH_SUMMARY_FAILURE_LINE.format(
                    issue_id=issue_id, detail=detail or "-"
                )
            )
    else:
        lines.append(messages.BATCH_SUMMARY_NO_FAILURES)

    if purge_record:
        lines.extend(["", messages.BATCH_SUMMARY_PURGE_HEADER, "", purge_record])

    return "\n".join(lines)
```

Add to `report/messages.py`:

```python
BATCH_SUMMARY_HEADER = "RESUMO DA MIGRAÇÃO EM LOTE — {label} (execução {run_id})"
BATCH_SUMMARY_TOTAL = "Itens na fila: {total}"
BATCH_SUMMARY_STATE = "  {state:<8}: {count}"
BATCH_SUMMARY_FAILURES = "FALHAS ({count}) — cada uma com o motivo:"
BATCH_SUMMARY_FAILURE_LINE = "  RDM {issue_id}: {detail}"
BATCH_SUMMARY_NO_FAILURES = "Nenhuma falha."
BATCH_SUMMARY_PURGE_HEADER = "REGISTRO DA LIMPEZA (FASE 0)"
BATCH_SUMMARY_SAVED = "Resumo salvo em {path}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_report.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add batch/report.py report/messages.py tests/test_batch_report.py
git commit -m "feat: batch run summary carrying the phase 0 record"
```

---

### Task 10: The Phase 1 CLI

**Files:**
- Create: `migrate_batch.py`
- Modify: `report/messages.py`
- Test: `tests/test_batch_runner.py`

**Interfaces:**
- Consumes: everything above, plus `main.run_preflight` and `main.load_yaml`
- Produces: `build_parser()`, `main(argv=None) -> int`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_batch_runner.py`:

```python
import migrate_batch  # noqa: E402


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_batch_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_batch'`

- [ ] **Step 3: Write minimal implementation**

Create `migrate_batch.py`:

```python
"""Phase 1: migrate a whole Redmine project in supervised, resumable batches.

    python migrate_batch.py --project hydro                  # dry-run
    python migrate_batch.py --project hydro --apply
    python migrate_batch.py --project projetos-telecom --apply --limit 200
    python migrate_batch.py --project projetos-telecom --apply --resume <run-id>

Run order is smallest first - operacao-cemig (4), hydro (170), then
projetos-telecom (5451). The first four cost minutes and show whether phase 0
was done right; starting with Telecom checks the same thing with a 5451-item
bill.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from batch.report import render_summary
from batch.runner import run_batch
from batch.selection import REDMINE_PROJECTS, pending_roots
from clients.errors import ApiError
from clients.glpi import GlpiClient
from clients.redmine import RedmineClient
from config.settings import DEFAULT_DB_PATH, ConfigError, load_settings, load_yaml
from main import run_preflight
from report import messages
from store.batch import BatchLedger

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_batch.py", description=messages.CLI_HELP_BATCH
    )
    parser.add_argument(
        "--project", required=True, choices=sorted(REDMINE_PROJECTS),
        help=messages.CLI_HELP_BATCH_PROJECT,
    )
    parser.add_argument("--apply", action="store_true", help=messages.CLI_HELP_APPLY)
    parser.add_argument("--yes", action="store_true", help=messages.CLI_HELP_YES)
    parser.add_argument("--limit", type=int, default=None, help=messages.CLI_HELP_BATCH_LIMIT)
    parser.add_argument("--resume", default=False, help=messages.CLI_HELP_BATCH_RESUME)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=messages.CLI_HELP_DB)
    parser.add_argument("--reports", default="reports", help=messages.CLI_HELP_BATCH_REPORTS)
    parser.add_argument("--purge-record", default=None, help=messages.CLI_HELP_BATCH_PURGE_RECORD)
    parser.add_argument("--skip-attachments", action="store_true", help=messages.CLI_HELP_SKIP_ATTACHMENTS)
    parser.add_argument("--skip-notes", action="store_true", help=messages.CLI_HELP_SKIP_NOTES)
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
        mapping = load_yaml("mapping.yml")
    except ConfigError as exc:
        print(messages.CONFIG_MISSING_VARS.format(names=str(exc)), file=sys.stderr)
        return EXIT_CONFIG
    messages.register_secrets(settings.secret_values())

    tracker = REDMINE_PROJECTS[args.project]
    print(messages.CLI_MODE_APPLY if args.apply else messages.CLI_MODE_DRY_RUN)

    try:
        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi, RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
            # Preflight ONCE for the whole batch, not per item: it opens the
            # session, widens the entity tree and loads every dropdown
            # dictionary. Repeating that 5451 times is hours of pure waste.
            if not run_preflight(glpi, redmine, mapping, issue_id=None):
                print(messages.PREFLIGHT_ABORTED, file=sys.stderr)
                return EXIT_FAILED

            with BatchLedger(args.db) as ledger:
                if args.resume:
                    run_id = str(args.resume)
                    queue = ledger.pending(run_id)
                else:
                    run_id = ledger.start_run(args.project)
                    queue = pending_roots(glpi, redmine, tracker, limit=args.limit)
                    ledger.queue(run_id, queue)

                print(messages.BATCH_QUEUE.format(count=len(queue), run_id=run_id))
                if not queue:
                    print(messages.BATCH_NOTHING_TO_DO)
                    return EXIT_OK

                if args.apply and not (args.yes or _confirm(len(queue))):
                    print(messages.APPLY_CANCELLED)
                    return EXIT_OK

                report_dir = Path(args.reports) / run_id
                run_batch(
                    glpi, redmine, mapping, ledger, run_id, queue,
                    apply_mode=args.apply, report_dir=report_dir, db_path=args.db,
                    skip_attachments=args.skip_attachments, skip_notes=args.skip_notes,
                )

                purge_record = None
                if args.purge_record and Path(args.purge_record).exists():
                    purge_record = Path(args.purge_record).read_text(encoding="utf-8")

                summary = render_summary(ledger, run_id, args.project, purge_record)
                summary_path = report_dir / "resumo.txt"
                summary_path.write_text(summary, encoding="utf-8")
                print()
                print(summary)
                print(messages.BATCH_SUMMARY_SAVED.format(path=summary_path))
                return EXIT_OK
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED


def _confirm(count: int) -> bool:
    try:
        answer = input(messages.BATCH_CONFIRM_PROMPT.format(count=count))
    except EOFError:
        return False
    return answer.strip().casefold() in messages.APPLY_CONFIRM_ACCEPT


if __name__ == "__main__":
    raise SystemExit(main())
```

Add to `report/messages.py`:

```python
CLI_HELP_BATCH = (
    "Migra em lote todos os projetos pendentes de um projeto do Redmine. "
    "Sem --apply apenas planeja."
)
CLI_HELP_BATCH_PROJECT = "Identificador do projeto no Redmine."
CLI_HELP_BATCH_LIMIT = "Migra no máximo N raízes nesta execução."
CLI_HELP_BATCH_RESUME = "Retoma a execução informada, refazendo pendentes e falhas."
CLI_HELP_BATCH_REPORTS = "Diretório dos relatórios (padrão: reports)."
CLI_HELP_BATCH_PURGE_RECORD = "Registro da fase 0, incorporado ao resumo final."
BATCH_QUEUE = "Fila: {count} raízes. Execução {run_id}."
BATCH_NOTHING_TO_DO = "Nada pendente: todas as raízes já estão no GLPI."
BATCH_CONFIRM_PROMPT = (
    "Isto vai criar até {count} projetos no GLPI. Digite 'sim' para continuar: "
)
```

- [ ] **Step 4: Adapt `run_preflight` for a batch**

`run_preflight(glpi, redmine, mapping, issue_id)` ends with `root_tracker_rejection`, which needs a single issue. Make the parameter optional so a batch can run preflight once without naming an issue — the per-item tracker is already guaranteed by `candidate_roots`, which only ever yields issues of an in-scope root tracker.

In `main.py`, change the signature to `issue_id: int | None = None` and guard the final step:

```python
    if issue_id is not None:
        rejection = root_tracker_rejection(redmine.fetch_issue(issue_id, include=()))
        if rejection:
            print(rejection, file=sys.stderr)
            return False
```

Run: `python -m pytest tests -q`
Expected: PASS — the existing preflight tests still pass because the default preserves current behaviour for every caller that passes an id.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_batch_runner.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: PASS, all 137 existing plus roughly 40 new

- [ ] **Step 7: Commit**

```bash
git add migrate_batch.py main.py report/messages.py tests/test_batch_runner.py
git commit -m "feat: phase 1 batch CLI with resume and per-run reports"
```

- [ ] **Step 8: Live dry-run, smallest project first**

Run: `python migrate_batch.py --project operacao-cemig`
Expected: a queue of 4 (or 6 if Phase 0 has run and removed the two CEMIG markers), no writes.

Then, only with the operator's go-ahead:

```bash
python migrate_batch.py --project operacao-cemig --apply
python migrate_batch.py --project hydro --apply --limit 20
```

Read `reports/<run-id>/resumo.txt` after each before widening the batch.

---

## Documentation

- [ ] **Update CLAUDE.md**

Add the two new commands to the Commands block, and a short section under Architecture describing the two phases, the poisoned-marker finding, the keep list, and the inverted delete order. Add the read-only Redmine invariant to Hard rules as rule 7.

- [ ] **Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the batch migration phases and the read-only invariant"
```

---

## Self-Review Notes

**Spec coverage:** every section of the design doc maps to a task — measurements → Task 3 constants; decision 1 (chunked runs) → Task 10 `--limit`/`--resume`; decision 2/3/4 (purge, force_purge, test ids) → Tasks 3–5; decision 5 (re-migrate the ~660) → falls out of Task 7 once the false markers are gone; decision 6 (no pre-measurement) → Task 5 Step 8 stops for a human; decision 7 (everything in the final report) → Task 9; decision 8 + invariant → Task 1.

**Known gap, deliberate:** `PurgeCounts.tasks` is wired but never incremented — the June projects have no ProjectTasks, and the flat `GET /ProjectTask` route returns nothing instance-wide, so there is no reliable way to enumerate them. If the live dry-run in Task 5 Step 8 shows tasks on any target, add the enumeration before applying.

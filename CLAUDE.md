# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A one-shot CLI that migrates a single Redmine issue tree into a GLPI project
(project + tasks + two Fields-plugin containers). The authoritative
specification is [INSTRUKCJA_Redmine_do_GLPI_2.md](INSTRUKCJA_Redmine_do_GLPI_2.md)
(Polish) — treat it as the source of truth; most modules cite it by section
number (`spec 6.5`, `spec 9.2`). [PROMPT_dla_Claude_Code.md](PROMPT_dla_Claude_Code.md)
holds the overriding scope decisions.

## Commands

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

python main.py --issue 20238                        # dry-run (default) — writes nothing
python main.py --issue 20238 --report report.txt    # dry-run + save the plan report
python main.py --issue 20172 --apply                # writes, after an interactive "sim" confirmation
python main.py --issue 20172 --apply --yes          # non-interactive confirmation (pipelines)
python main.py --issue 20238 --db path\to\other.db  # override the SQLite map (default: migration.db)

python audit_coverage.py                # dropdown coverage audit, tracker 14 (read-only)
python audit_coverage.py --tracker 42
```

Exit codes: `0` ok, `1` failed/aborted, `2` configuration error.

There is no test suite, linter, or build step. Verification is done by running
the dry-run against the 11-issue test list in spec section 1a — notably
`20238` (minimal), `20156`/`18620`/`18826` (out-of-scope child rule), and
`20172` (the only issue exercising the Faturamento/container-25 path).

Credentials come from `.env` (copy `.env.example`). `REDMINE_URL` is a bare host;
`apirest.php` belongs to `GLPI_URL` only — `load_settings()` rejects that mix-up.

## Architecture

The pipeline is strictly read → plan → report → (optionally) write, and
[main.py](main.py) is the only place the phases meet:

1. **Preflight** (`run_preflight`, spec 9.0) — GLPI session, Fields-plugin
   rights (`ERROR_RIGHT_MISSING` is a hard stop), one GET per dropdown
   dictionary into `GlpiClient._dropdown_cache`, Redmine reachability.
2. **Dedup** (`check_already_migrated`, spec 9.1) — searches GLPI for the
   `rdmfield` marker. Redmine and GLPI ids are independent, so the marker, not
   the id, is authoritative. v1 refuses to touch an already-migrated project.
3. **Plan** (`build_project_plan`) — reads the whole tree, produces a
   `ProjectPlan`. Pure; nothing is written.
4. **Report** (`report/reporter.py`) — always rendered before any write.
5. **Apply** (`apply_plan`) — only with `--apply` + confirmation.

Module roles:

- [clients/](clients/) — `RedmineClient` (`X-Redmine-API-Key`) and `GlpiClient`
  (context manager: `initSession` → `Session-Token`/`App-Token` → `killSession`).
  `GlpiClient` also owns the dropdown cache and the container-row helpers.
- [transform/tree.py](transform/tree.py) — assigns each node a `Disposition`
  (PROJECT / TASK / FATURAMENTO / SKIPPED).
- [transform/mapper.py](transform/mapper.py) — applies `config/mapping.yml`,
  returning both a payload and a `FieldRecord` per field.
- [transform/faturamento.py](transform/faturamento.py) — finds tracker-15
  issues linked to the root via relations.
- [resolve/](resolve/) — status, user, and dropdown name→id lookups; all three
  return `None` on a miss rather than raising or inventing a value.
- [store/db.py](store/db.py) — SQLite `migration_map` for idempotence and
  resumability. A cache and crash guard, *not* a replacement for the GLPI
  `rdmfield` check.
- [report/messages.py](report/messages.py) — every user-facing string, plus
  `register_secrets()` / `redact()`.

### Write order (fixed by spec 9.2)

`POST /Project` → container-15 row → tasks parent-before-child → container-25
rows. Tasks always set `projects_id` to the root project; `projecttasks_id`
comes from the parent's entry in `plan.glpi_ids`. Container 15 is type "dom"
(one row per project, so `write_additional_fields_row` updates if the plugin
already created it); container 25 is type "tab" (many rows). If GLPI rejects a
second container-25 row, apply degrades: keep the first, report the rest, do
not abort.

### The reporting contract

Reporting every skipped field is the primary functional requirement (spec 13,
rule 7), not an add-on. `Mapper` therefore never returns a bare payload — it
emits a `FieldRecord` with an `Outcome` (`WRITTEN`, `EMPTY_SOURCE`,
`NO_COUNTERPART`, `UNRESOLVED`, `NEVER_WRITE`) for every field it touched *and*
a sweep pass over every source custom field no mapping entry consumed. When
adding a code path that reads source data, add the corresponding records —
nothing may disappear silently. The same rule covers unreachable children,
cyclic trees, and ignored relations, which are all carried on `ProjectPlan`.

## Hard rules (spec 13 — do not relax these)

1. **Match custom fields by name, never by id.** Ids differ per tracker: the
   spec's own "index 9" claim would have written a year into the billing field.
2. **Never auto-create a dropdown entry or an entity.** No match → skip the
   field + warn. 9 of the 12 dictionaries are empty on the live instance, so
   this path is the common case, not the exception.
3. **Never write to a field with `is_active: 0`** (declared under `never_write`
   in `mapping.yml`).
4. **Dry-run is the default.** No write without `--apply` *and* a confirmation
   shown after the full report.
5. **Tokens only from env/.env**, never in code, logs, or error text. Anything
   user-facing goes through `messages.redact()`; new secrets must be passed to
   `messages.register_secrets()`.
6. **Don't guess.** Anything not in the spec gets a `TODO` with a comment, or a
   question to the user — plus a dated verification comment when confirmed
   against the live API.

## Language convention

User-facing text — CLI help, report body, error messages — is **PT-BR**, and
lives in `report/messages.py`. Code identifiers and comments are **English**;
migrated data (field names, values) is copied verbatim, never translated.

## Scope (closed decisions)

In scope as migration roots and tasks: trackers **14** (Projeto) and **42**
(Projeto Hydro). Tracker **15** (Faturamento) becomes a container-25 row on the
root project — the only tracker that drives branching logic. Out of scope:
**39** CEMIG and **40** Subtarefa Cemig (entirely), **41** Compras, **18**
Atividades (phase two), and attachments (phase two). An out-of-scope child is
not created in GLPI; it gets an explicit report line, and its descendants are
skipped too. Scope lives in `config/settings.py`
(`IN_SCOPE_TASK_TRACKERS`, `IN_SCOPE_ROOT_TRACKERS`, `TRACKER_FATURAMENTO`).

## Verified traps (real data, don't "simplify" these away)

- `include=children` returns only id/tracker/subject — each child needs its own
  GET. The `children` key may be absent entirely; always `.get("children", [])`.
- Relation direction varies: the partner is the field that is **not** the
  current issue id (`relation_partner_id`). `relates` also links Projeto↔Projeto,
  so only tracker-15 partners may be migrated.
- Numeric values are copied verbatim after `.strip()`, never parsed:
  "Valor do Projeto" uses `66.977,45` while "Valor Total da NF" uses `5593.40`,
  and both target GLPI text columns.
- Dropdown comparison is `.strip()` + casefold — GLPI stores `MÉDIA`, Redmine
  sends `Média`. Dropdown itemtypes appear in two capitalisations
  (`...fielddropdown` / `...fieldDropdown`); `load_dropdown` falls back.
- GLPI `searchText` is a substring match, so `rdmfield=2023` would also match
  issue 20238 — results are always filtered for exact equality.
- Empty source values must be tested by falsiness, not `== ""`; real data has
  `null` where other issues have `""`.
- `entities_id` is deliberately not sent: verified 2026-07-30 that GLPI places
  the project in the session's active entity (75, TIVIT > SMART SYSTEMS).

## Open points before production (spec 11)

Five container-15 columns are flagged mandatory
(`MANDATORY_CONTAINER15_COLUMNS`); the dry-run report surfaces missing ones.
Tracker 14 covers all five; tracker 39 does not — unresolved, and one reason 39
stays out of scope. Also undecided: `plan_end_date` source (currently
`DATA TERMINO PLANEJADA` → fallback `due_date`) and `real_end_date`
(currently `Data Finalização` only; `closed_on` is reported, never written).

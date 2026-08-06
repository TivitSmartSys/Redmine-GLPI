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

There is a small pytest suite (`python -m pytest tests -q`) covering the confirm
gate and the summary figures; there is no linter or build step. Real
verification is the dry-run against the test list in spec section 1a — notably
`20238` (minimal), `18620` (out-of-scope child rule, tracker 41), and `20156`
(a tracker-18 child, in scope since 2026-08-06).

Most of that list is now migrated, so dedup stops the dry-run before it plans
anything. Pick a fresh issue when you need to exercise a path end to end:
`20438`, `20280`, `20380` and `20441` each have a tracker-15 partner by
relation, and `19403` has seven tracker-18 children.

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
  issues linked to the root via relations. The other path (a tracker-15
  descendant) is handled by the tree walk.
- [resolve/](resolve/) — status, user, and dropdown name→id lookups; all three
  return `None` on a miss rather than raising or inventing a value.
- [store/db.py](store/db.py) — SQLite `migration_map` for idempotence and
  resumability. A cache and crash guard, *not* a replacement for the GLPI
  `rdmfield` check.
- [report/messages.py](report/messages.py) — every user-facing string, plus
  `register_secrets()` / `redact()`.

### Write order (fixed by spec 9.2)

`POST /Project` → container-15 row → tasks parent-before-child → per Faturamento
a `POST /ProjectTask` then its container-26 row. Tasks always set `projects_id`
to the root project; `projecttasks_id` comes from the parent's entry in
`plan.glpi_ids`.

**The `POST /Project` input carries the container-15 values** — see
`project_create_payload` in [main.py](main.py). Container 15 is type "dom", so
its fields belong to the Project's own form and the plugin validates its
mandatory columns inside the Project's add hook, reading them from the Project
input. Sending core columns alone makes GLPI reject the project itself with
`ERROR_GLPI_ADD "Alguns campos obrigatórios estão vazios : …"`. The plugin then
creates the container row from that same input, so `write_additional_fields_row`
takes its update branch — which is what fills in the columns the plugin skips,
`rdmfield` among them.

Container 26 is type "tab" and is written straight to the container itemtype
over REST, a path that never reaches the plugin's `validateValues()`. Its five
mandatory flags therefore do **not** refuse the write; a missing value only
leaves the row incomplete. Report section 5 keeps the two cases in separate
blocks and must stay that way. If GLPI rejects a container-26 row, apply
degrades: keep the task, report the values, do not abort.

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
   field + warn. Most dictionaries were populated on 2026-08-03, but a populated
   dictionary is not full coverage — an unmatched value is still skipped and
   reported, never invented.
3. **Never write to a field with `is_active: 0`** (declared under `never_write`
   in `mapping.yml`) — with one deliberate exception, `rdmfield`. Note the
   reason: verified in plugin source 1.24.3 that such a write is **not**
   rejected (`populateData()` has no `is_active` filter and a direct row write
   skips validation entirely); the value simply becomes invisible in the GLPI
   form. `rdmfield` flipped to `is_active: 0` and back to `1` during 2026-08-06;
   it is written either way because it is the dedup marker, and
   `searchText[rdmfield]` is unaffected by the flag — verified live against
   projects 1265/1266 while the field was inactive.
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

In scope as migration roots: trackers **14** (Projeto) and **42** (Projeto
Hydro). As tasks: 14, 42 and **18** (Atividades). Tracker **15** (Faturamento)
becomes a ProjectTask of type *Faturamento* on the **root** project — never
nested under its Redmine parent — carrying a container-26 row; it is the only
tracker that drives branching logic. Out of scope: **39** CEMIG and **40**
Subtarefa Cemig (entirely), **41** Compras, and attachments (phase two). An
out-of-scope child is not created in GLPI; it gets an explicit report line, and
its descendants are skipped too. Scope lives in `config/settings.py`
(`IN_SCOPE_TASK_TRACKERS`, `IN_SCOPE_ROOT_TRACKERS`, `TRACKER_FATURAMENTO`,
`TRACKER_ATIVIDADES`, `TRACKER_TO_PROJECTTASKTYPE`).

Task types come from `TRACKER_TO_PROJECTTASKTYPE` (15 → 3 Faturamento, 18 → 4
Atividade). Trackers 14 and 42 deliberately have no entry, so an untyped task is
reported as `NO_COUNTERPART` rather than as a lookup that failed.

Container **16** (`camposadicionaistarefasdeprojeto`, type "dom" on ProjectTask)
is active but deliberately **not written**: three of its five fields are
`is_active: 0` and the dictionary behind the one real counterpart
(`tipodesitefield` ← "Tipo de Site") is empty, so every value would be skipped
anyway. Atividades keep the spec-9.4 comment dump. Because it is a "dom"
container, the moment any of its fields is flagged mandatory `POST /ProjectTask`
starts failing exactly as `POST /Project` did — the fix would be the same merge
`project_create_payload` does.

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
- `status_map.yml` must cover the trackers actually in scope. It was written for
  Projeto only, so when tracker 15 became a task the three Faturamento statuses
  (19 `NF Emitida`, 21 `NF Paga`, 22 `NF Solicitada`) were missing and 3036 of
  3414 tasks — 88.9% — would have been created with no Estado. Check the map
  against real status counts whenever a tracker enters scope.
- `glpi_projectstates` contains duplicates: `Novo` at ids 1 and 12, and
  `NF solicitada` (14) next to `NF Solicitada` (17). The map targets 1 and 17;
  ids 12 and 14 should be merged away on the GLPI side.

## Open points before production (spec 11)

Five container-15 columns are flagged mandatory
(`MANDATORY_CONTAINER15_COLUMNS`); missing ones **refuse the project**, and the
dry-run report surfaces them. Tracker 14 covers all five; tracker 39 does not —
unresolved, and one reason 39 stays out of scope.

Container 26 has five mandatory columns too
(`MANDATORY_CONTAINER26_COLUMNS`). Four are mapped. The fifth,
`plugin_fields_statusfaturamentofielddropdowns_id` ("Status Faturamento"), is
deliberately **not** written: the only sensible source is the core Redmine
status, and that already reaches the task's own **Estado** column
(`projectstates_id`) through `status_map.yml`. Filling both would mean keeping
the same five values in two dictionaries forever.

**Open GLPI-side request:** field 225 is still `mandatory: 1` and its dictionary
`PluginFieldsStatusfaturamentofielddropdown` is empty. Ask an admin to unflag
it, then drop the column from `MANDATORY_CONTAINER26_COLUMNS`. Until then report
section 5 lists it on every Faturamento — on purpose, so the request stays
visible. It does not block the write (see the write-order section).

Do not confuse the two fields. **Estado** on a ProjectTask is core GLPI
(`glpi_projectstates`); **Status Faturamento** is a plugin dropdown shown on the
task's *Faturamento* tab. They look interchangeable in the UI and are not.

Watch the column spelling too: a plugin dropdown field named `x` stores into
`plugin_fields_xdropdowns_id`, while every other field type keeps the bare name.

**Tasks have no dedup marker.** Container 15 has `rdmfield`; containers 16 and
26 have no equivalent (container 16's table holds an orphan `idrdmfieldtwo`
column with no field definition behind it). Re-running therefore relies on the
local SQLite map alone for tasks, which is a crash guard, not an authority —
the biggest exposure now that 1259 Atividades and 3414 Faturamentos are in
scope. The fix is a GLPI-side request: add an `rdmfield` text field to container
16.

`responsvelprojetofield` needs **two** fixes before it can be mapped:
`is_active: 1` **and** a populated `PluginFieldsResponsvelprojetofielddropdown`
(0 entries on 2026-08-06). Activating the field alone changes nothing under
rule 2.

Also undecided: `plan_end_date` source (currently `DATA TERMINO PLANEJADA` →
fallback `due_date`) and `real_end_date` (currently `Data Finalização` only;
`closed_on` is reported, never written).

**The instance keeps moving.** Container 25 read `is_active: 0` and `1` within
the same day; the mandatory flags and the deactivation of fields 92/93/94 all
landed after `mapping.yml` was last verified. Re-run `python audit_coverage.py`
before a production run rather than trusting the dated comments in
`mapping.yml`.

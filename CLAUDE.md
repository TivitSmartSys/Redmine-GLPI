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
python main.py --issue 16467 --apply --skip-attachments  # no file uploads (still reported)
python main.py --issue 16467 --apply --skip-notes        # no Notepad rows (still reported)

python audit_coverage.py                # dropdown coverage audit, tracker 14 (read-only)
python audit_coverage.py --tracker 42

python reset_migration.py --issue 16467              # diagnose only — deletes nothing
python reset_migration.py --issue 16467 --apply      # forget the migration, after "sim"
python reset_migration.py --issue 16467 --local-only # clear the SQLite map, leave GLPI alone
```

`reset_migration.py` exists because deleting the project in GLPI does **not**
unblock a re-run: the container-15 row carrying `rdmfield` outlives its project,
so `check_already_migrated` keeps finding it. It clears both caches — the
orphaned marker and the `migration_map` rows, whose ids it reads from Redmine
because the table cannot reconstruct a tree on its own. Those ids include the
**attachment** ids and the **journal** ids: a `Document` row is keyed by the
attachment and a `Notepad` row by the journal, not by the issue, so listing
issue ids alone would leave every document and every note row behind. It refuses
while the host project is alive; a project in the trash counts as orphaned.

Exit codes: `0` ok, `1` failed/aborted, `2` configuration error.

There is a pytest suite (`python -m pytest tests -q`, 93 tests) covering the
confirm gate, the summary figures, the VARCHAR(255) truncation, and the
attachment and note phases — host assignment, each section's own arithmetic, the
line-anchored markers and apply degradation. There is no linter or build step.
Real
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
- [transform/attachments.py](transform/attachments.py) and
  [transform/notes.py](transform/notes.py) — plan the files and the notes. Both
  pure. `notes.py` imports `host_for`/`item_label` from `attachments.py` rather
  than repeating them: the disposition→host rule is one rule and must not drift.
- [resolve/](resolve/) — status, user, and dropdown name→id lookups; all three
  return `None` on a miss rather than raising or inventing a value.
- [store/db.py](store/db.py) — SQLite `migration_map` for idempotence and
  resumability. A cache and crash guard, *not* a replacement for the GLPI
  `rdmfield` check.
- [report/messages.py](report/messages.py) — every user-facing string, plus
  `register_secrets()` / `redact()`.

### Write order (fixed by spec 9.2)

`POST /Project` → container-15 row → tasks parent-before-child → per Faturamento
a `POST /ProjectTask` then its container-26 row → **attachments** (step 5, added
2026-08-10) → **notes** (step 6, added 2026-08-11). Tasks always set
`projects_id` to the root project; `projecttasks_id` comes from the parent's
entry in `plan.glpi_ids`.

Attachments and notes come last because every host item must already exist. Step
4 now also registers each Faturamento task in `plan.glpi_ids` — it used to keep
the id only on the item, which left a Faturamento's files unable to find their
host (and the tree in the report showing no GLPI id for those rows).

Notes come after the files, not before: a note names the attachments that
arrived with it, so by the time it is written those documents are already in
GLPI. Step 6 needs no `RedmineClient` — the journal text came down with the tree
during the plan phase, which is why `apply_notes` takes no source client while
`apply_attachments` does.

### Attachments → GLPI Documents

Opened 2026-08-10; spec section 3 had deferred them ("`Documentos` — poza
zakresem v1"), so nothing here comes from the spec.

A Redmine attachment becomes a `Document` linked through `Document_Item` to the
item its issue became: root → the Project, tracker 18/41 → their ProjectTask,
tracker 15 → its Faturamento ProjectTask (container 26 has no file column, so
the files hang off the task itself). An attachment on an out-of-scope issue is
**reported and not migrated** — the same rule as the issue itself.

[transform/attachments.py](transform/attachments.py) plans this and is pure:
the dry-run takes filenames and sizes from the Redmine metadata and downloads
nothing. `--skip-attachments` still lists every file, marked as skipped.

Verified live 2026-08-10 (GLPI 11.0.6) by uploading one file to a throwaway
project/task and purging it: `POST /Document` over the legacy `apirest.php`
works, and so does a `Document_Item` link whose `itemtype` is `ProjectTask` —
which had zero rows on the instance, so it was genuinely untested. `document`
right is 255, `document_max_size` is 50 MB, and `glpi_documenttypes` has 76 rows
including `.xlsb`/`.msg`/`.eml`/`.ods`, all four of which RDM 16467 needs.

**Documents have a dedup marker; tasks still do not.** `Document.comment` carries
`rdmattachment:<attachment id>` on its own first line, and
`find_document_by_marker` is the document twin of `find_by_rdmfield`. GLPI is the
authority, `migration_map` (itemtype `Document`, `redmine_id` = the *attachment*
id) is the crash guard. Losing `migration.db` therefore cannot duplicate files,
which is exactly the exposure containers 16 and 26 still have.

Proven end to end on 2026-08-10 with RDM 16467 → project 1277: 23 files
uploaded (19 on the project, 3 on the Compras task 14107, 1 on the Faturamento
task 14109), then `apply_attachments` re-run against an **empty** SQLite map —
23/23 came back `DEDUP_GLPI`, nothing was uploaded and no link was duplicated.

### Notas → GLPI Notepad

Opened 2026-08-11, the last content phase. The spec does not mention journals at
all, so like attachments nothing here comes from it.

A Redmine **journal entry that carries text** becomes a `Notepad` row — the
"Notas" tab — on the item its issue became, by exactly the host rule the files
follow. Journal entries with no text (a status change, a custom-field edit) are
the majority: RDM 16467 has 108 journals across its tree and only 17 with text.
They are **not** written and **not** listed one per line; they get one aggregate
count per host in report section 9, so nothing vanishes without the reader
seeing it. A note on an out-of-scope issue is reported and not migrated.

A note's text is copied verbatim. Everything the migration adds rides in a
header above it: the marker, the provenance, the author and date, `[NOTA PRIVADA
no Redmine]` when the source entry was private, and the names of the files that
arrived with that note. **Private notes are migrated with that visible tag** —
a product decision taken 2026-08-11; GLPI's Notepad has no privacy flag, so
saying so in the text is the only honest option.

The note↔file association exists **only** in the journal's own `details` rows
(`property: "attachment"`, `name` = the attachment id, `new_value` = the
filename). The issue's flat `attachments` list cannot answer which note brought
what. The bytes are still migrated once, by step 5; the note only names them.

Verified live 2026-08-11 (GLPI 11.0.6): `GET /Project/<id>/Notepad` and
`GET /ProjectTask/<id>/Notepad` both answer with a list, `GET /Notepad` returns
rows for both itemtypes, and a flat `POST /Notepad` carrying
`{itemtype, items_id, content}` was accepted for both hosts — row 4 on Project
1277 and row 5 on ProjectTask 14107, each read back and purged again.
`getActiveProfile` has **no `notepad` key at all**: a Notepad row rides on the
host item's own right, so unlike container 26 this needed no grant.

Proven end to end on 2026-08-11 with RDM 1240 → project 1280: 37 notes written
(36 on the project, 1 on the Faturamento task of RDM 1126, reached by relation),
then `apply_notes` re-run against a Notepad-free SQLite map — 37/37 came back
`DEDUP_GLPI`, nothing was written and the project still held exactly 36 rows.
Thirty-six is also the number that proves `notepad_rows` asks for a `range`: a
truncated read would have seen 15 and duplicated the other 21.

**Notes have a dedup marker; tasks still do not.** `Notepad.content` carries
`rdmnote:<journal id>` on its own first line and `find_notepad_by_marker` is the
note twin of `find_document_by_marker`, with one deliberate difference: it is
scoped to a single host item. A Notepad row cannot outlive the item it hangs
off, so "already migrated" can only ever mean "already on this item" — which is
also why `reset_migration.py` has nothing to clean on the GLPI side for notes,
only the local map.

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

Attachments and notes obey the same rule through **parallel** mechanisms, not
the same one: `AttachmentOutcome` with report section 8, and `NoteOutcome` with
report section 9, each closing its own arithmetic. Both are deliberately absent
from `ProjectPlan.all_records()` — section 7 proves every source *field* landed
in exactly one bucket, and neither a file nor a note is a field. Folding either
in would break the one number the report exists to guarantee. The three sections
are independent proofs and must stay that way.

Watch the field name on `ProjectPlan`: `notes` is the apply-time message log
(`list[str]`, appended to the end of the report) and predates this phase. The
planned notes live on **`notes_planned`**.

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
Hydro). As tasks: 14, 42, **18** (Atividades) and **41** (Compras). Tracker
**15** (Faturamento) becomes a ProjectTask of type *Faturamento* on the **root**
project — never nested under its Redmine parent — carrying a container-26 row;
it is the only tracker that drives branching logic. Out of scope: **39** CEMIG
and **40** Subtarefa Cemig (entirely). Attachments left phase two on 2026-08-10
and are now migrated as GLPI Documents; notes followed on 2026-08-11 as GLPI
Notepad rows — see both sections above. An
out-of-scope child is not created in GLPI; it gets an explicit report line, and
its descendants are skipped too — its files and its notes are reported and left
behind with it. Scope lives in `config/settings.py`
(`IN_SCOPE_TASK_TRACKERS`, `IN_SCOPE_ROOT_TRACKERS`, `TRACKER_FATURAMENTO`,
`TRACKER_ATIVIDADES`, `TRACKER_COMPRAS`, `TRACKER_TO_PROJECTTASKTYPE`).

Task types come from `TRACKER_TO_PROJECTTASKTYPE` (15 → 3 Faturamento, 18 → 4
Atividade, 41 → 5 Compras). Trackers 14 and 42 deliberately have no entry, so an
untyped task is reported as `NO_COUNTERPART` rather than as a lookup that failed.

Compras is a task tracker only, never a root. Of its 72 issues, 56 hang directly
off a tracker-14 or tracker-42 root and are migrated; the other **16 have no
parent at all** and are knowingly left unmigrated — nothing reaches them. Their
seven custom fields (`Cliente`, `Data Finalização`, `Data Cotação`, `Solicitação
Interna`, `Código Solicitação Interna`, `Data Aprovação FP&A`, `Pedido`) have no
column in `glpi_projecttasks` and go to the spec-9.4 comment dump, exactly as
Atividades do. All four statuses Compras uses were already in `status_map.yml`.

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
  The same holds for `attachments`, which until 2026-08-10 was **not requested
  for descendants at all** (`_expand` used `include=("children",)` and
  `discover_from_relations` used `include=()`), so every child and every
  relation-sourced Faturamento looked like it had no files whatsoever.
- **`journals` has to be requested in all three of those places too**, and it is
  the same trap one level further: `DEFAULT_INCLUDE`, `_expand` and
  `discover_from_relations`. Miss any one and the root keeps its notes while
  every task and every relation-sourced Faturamento silently reads as having
  none. The proof it works is RDM 16467, whose notes land on the project (14),
  on the Compras task 19769 (2, descendant path) and on the Faturamento 17539
  (1, relation path) — one note on each of the three paths.
- **A multipart upload has to clear this client's own `Content-Type`.**
  `GlpiClient.__init__` sets `application/json` on the session, and `requests`
  only computes a multipart boundary when the header is absent — so
  `_upload()` passes `Content-Type: None` per request. Without it GLPI receives
  a body it cannot parse and reports an empty upload, which reads like a rights
  problem and is not one. This is why uploads bypass `_request()`.
- `searchText[comment]` finds a document marker, but it is the same substring
  match as everywhere else: `rdmattachment:2931` would match
  `rdmattachment:29314`. `_has_exact_marker` compares whole **lines**, which is
  why the marker is always written as the comment's first line on its own. The
  note marker repeats this exactly: `rdmnote:` on its own first line of
  `Notepad.content`, never folded into the readable author/date line — that line
  is rebuilt from Redmine on every run, and a marker that moved with it would
  stop matching and turn dedup into duplication.
- `notepad_rows` needs `range` for the same reason `document_links` does. It
  reads through the host sub-item route (`GET /Project/12/Notepad`), which
  scopes the answer by construction, so unlike the searchText helpers it has
  nothing to re-filter for exact equality afterwards.
- **A GLPI list GET returns only the first 15 rows without an explicit `range`.**
  Measured 2026-08-10: project 1277 has 19 linked documents and `document_links`
  read back 15. The four missing rows existed the whole time — the *reader* was
  truncated. That is why `_search` takes `full_range` (`SEARCH_FETCH_RANGE`);
  without it `_ensure_link` would post duplicates for anything past the 15th
  file on a re-run.
- GLPI refuses a duplicate `Document_Item`, so a re-run must check
  `document_links` before posting one — otherwise the refusal reads as a real
  failure. Deleting the GLPI project purges its links but **not** the documents;
  their markers survive, so a re-migration re-links them instead of re-uploading
  (55 MB, in the case of 17582).
- `glpi_documenttypes.ext` holds patterns in some rows, so a plain string miss
  can be a false alarm. An unknown extension is therefore a report **warning**
  and the upload is attempted anyway — GLPI stays the authority.
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
- **Writing a container row needs UPDATE on the host itemtype's right, and a
  successful `GET /PluginFieldsField` does not prove it.** Diagnosed 2026-08-07:
  container-26 rows were refused with `ERROR_GLPI_ADD "Você não tem permissão
  para executar essa ação."` because the profile had `project` = 1151 but
  `projecttask` = 1145 — missing exactly UPDATE(2) and CREATE(4). Isolated by
  elimination: a payload with no data column at all is still refused (not the
  values), adding `entities_id: 75` changes nothing (not the entity), the same
  shape on container 25 with a `Project` host succeeds (not the code), and
  `PUT /ProjectTask/<id>` succeeds because GLPI honours "update my own tasks"
  there while the plugin wants the plain UPDATE bit. That last one is why the
  symptoms look self-contradictory. **RESOLVED the same day**: `projecttask` was
  raised to 1151 and a container-26 row now writes with its values.
  `can_write_projecttask_containers()` reads the bit from `GET /getActiveProfile`
  and preflight **warns** — never aborts, since only the Faturamento tab is lost.
- **Profile rights are not columns on `glpi_profiles`.** `GET /Profile/4` shows
  `projecttask` as a key, but `PUT /Profile/4` with `{"projecttask": 1151}`
  returns `{"4": true}` and changes **nothing** — a silent no-op that reads as
  success. Rights live in `glpi_profilerights`, one row per right: find it with
  `GET /Profile/<id>/ProfileRight` and `PUT /ProfileRight/<row id>` with
  `{"rights": <mask>}`. That is how the 2026-08-07 grant was applied (row 194).
- **A plugin `text` column is a VARCHAR(255), and going over it leaves an orphan
  project.** Diagnosed 2026-08-11 on RDM 17444, whose "Andamento do Projeto"
  holds 446 characters: `POST /Project` answered `ERROR_GLPI_ADD "MySQL query
  error: Data too long for column 'andamentodoprojetofield' (1406)"`. The trap
  is not the refusal, it is the order — **GLPI had already committed project
  1279** and only then did the plugin's add hook fail on its own INSERT. The
  project survived with no container row, hence no `rdmfield`, hence invisible
  to dedup: a retry would have created a second one. That is the exact opposite
  of the mandatory-field case above, where the plugin's *validation* refuses the
  project before anything is written. Do not conflate the two. A sweep of
  trackers 14 and 42 found **92 of 5589 issues** over the limit, worst 2788
  characters (RDM 2313). Closed decision 2026-08-11: `Mapper` truncates at
  `PLUGIN_TEXT_MAX_LENGTH` for `PLUGIN_CONTAINER_SECTIONS` only, sets
  `FieldRecord.truncated`, and the report grows a "CAMPOS CORTADOS" block
  showing both the kept and the dropped half. Core columns (`content`,
  `comment`) are TEXT and are never cut. The permanent fix is GLPI-side: change
  field 186 from the plugin's `text` type to `textarea`, which is backed by TEXT.
- **An attachment can 404 while the Redmine host is perfectly fine.** RDM 1240's
  77 attachments (2016-2017) all answered 404 on `content_url` while RDM 16467's
  answered 200 from the same host in the same run — the old files are gone from
  Redmine's disk. This is data, not configuration: `--apply` degraded exactly as
  designed, reporting all 77 as `FAILED_DOWNLOAD` while the project, its tasks
  and its 37 notes were written. Do not go hunting for a broken `REDMINE_URL`.
- **A permission error can mean the parent is gone.** `POST /ProjectTask` with a
  `projects_id` that no longer exists fails with the same "Você não tem
  permissão" text as a real rights problem, and so does a container row whose
  host item was deleted. Confirm the parent still exists before blaming rights —
  on 2026-08-07 a deleted project sent this diagnosis down a false trail.

## Open points before production (spec 11)

Five container-15 columns are flagged mandatory
(`MANDATORY_CONTAINER15_COLUMNS`); missing ones **refuse the project**, and the
dry-run report surfaces them. Tracker 14 covers all five; tracker 39 does not —
unresolved, and one reason 39 stays out of scope.

`MANDATORY_CONTAINER26_COLUMNS` is **empty since 2026-08-07**: a sweep of
`GET /PluginFieldsField` found `mandatory: 0` on every field of container 26 —
including field 225 ("Status Faturamento"), whose unflagging was the standing
GLPI-side request. Report section 5 no longer lists it. Field 225 stays in
`never_write` for the reason that has not changed: the task's core **Estado**
already carries the same status.

The same sweep found container 15's five columns at `mandatory: 0` as well, yet
`MANDATORY_CONTAINER15_COLUMNS` is deliberately left populated — a container-15
flag *refuses the project*, the flags have flipped before, and the tuple only
costs a report line.

### Faturamento stays on container 26 — CLOSED, confirmed with the manager 2026-08-07

A Faturamento is a ProjectTask of type *Faturamento* whose invoice data lives on
its **Faturamento tab** (container 26). This is a product decision, not a
technical one; do not re-open it on technical grounds.

**Therefore the `projecttask` UPDATE right is a hard prerequisite, not a
workaround.** It was granted on 2026-08-07 (profile 4 Super-Admin, `projecttask`
1145 → 1151) and verified end to end: a container-26 row created on a ProjectTask
with its values stored. Should it ever be revoked, every Faturamento is created
as an empty shell — name, Estado and Tipo, with none of the invoice values.
Preflight warns (`PREFLIGHT_PROJECTTASK_RIGHT_MISSING`); it does not abort,
because a project with no Faturamento is unaffected.

**Rejected alternative, kept because the evidence is expensive to re-derive.**
Moving the invoice fields into container 16 (the "dom" container on ProjectTask)
*would* sidestep the missing right: a "dom" container's values ride inside the
`POST /ProjectTask` input and the plugin writes the row from GLPI's own add
hook, which never reaches the REST rights check. Proven live on 2026-08-07 —
`POST /ProjectTask` carrying `plugin_fields_prioridadefielddropdowns_id: 7`
produced container-16 row 5883 with that value, in entity 75, while the profile
still lacked the right; the throwaway task was purged afterwards. It was dropped
because the manager confirmed the tab is what the business wants, and because it
carried two real costs: there is **no repair path** (`PUT` on a ProjectTask
container row is refused too, so everything must land in one `POST`, with no
equivalent of `write_additional_fields_row`'s update branch), and container 16
is **shared by every ProjectTask**, so one field flagged mandatory would break
`POST /ProjectTask` for Atividades and Compras as well.

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

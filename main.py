"""CLI entry point: migrate one Redmine issue tree into GLPI.

Dry-run is the default. Nothing is written to GLPI without the explicit --apply
flag and an interactive confirmation shown after the full plan report
(spec section 9.2, rule 6).

STAGE 1: API clients + preflight only. The migration logic follows in the next
stage, per the agreed work order.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# Allow `python main.py` from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.errors import ApiError, GlpiRightMissingError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402
from config.settings import (  # noqa: E402
    DEFAULT_DB_PATH,
    DOCUMENT_MARKER_PREFIX,
    ITEMTYPE_DOCUMENT,
    ITEMTYPE_FATURAMENTO,
    ConfigError,
    load_settings,
    load_yaml,
)
from report import messages  # noqa: E402
from report.reporter import (  # noqa: E402
    PlannedFaturamento,
    PlannedTask,
    ProjectPlan,
    Reporter,
    SkippedChild,
)
from resolve.dropdowns import DropdownResolver  # noqa: E402
from resolve.status import StatusResolver  # noqa: E402
from resolve.users import UserResolver  # noqa: E402
from store.db import STATUS_OK, MigrationStore  # noqa: E402
from transform.attachments import (  # noqa: E402
    AttachmentOutcome,
    plan_attachments,
)
from transform.faturamento import discover_from_relations  # noqa: E402
from transform.mapper import Mapper  # noqa: E402
from transform.tree import Disposition, plan_tree  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=messages.CLI_DESCRIPTION,
    )
    parser.add_argument("--issue", type=int, required=True, help=messages.CLI_HELP_ISSUE)
    parser.add_argument("--apply", action="store_true", help=messages.CLI_HELP_APPLY)
    # Non-interactive confirmation. Dry-run stays the default and writing still
    # needs an explicit flag - this only replaces the typed "sim" for pipelines
    # where stdin is not a terminal.
    parser.add_argument("--yes", action="store_true", help=messages.CLI_HELP_YES)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=messages.CLI_HELP_DB)
    parser.add_argument("--report", default=None, help=messages.CLI_HELP_REPORT)
    # Attachments migrate by default: a project without its files is not the
    # migration anyone asked for. The flag exists for a quick run or a flaky
    # network - the files still appear in report section 8, marked as skipped.
    parser.add_argument(
        "--skip-attachments",
        action="store_true",
        help=messages.CLI_HELP_SKIP_ATTACHMENTS,
    )
    return parser


def dropdown_itemtypes(mapping: dict) -> list[str]:
    """Every dictionary itemtype referenced by mapping.yml.

    Derived from the mapping so preflight loads exactly one dictionary per
    itemtype (spec 9.0 step 3: one call per dictionary, not per field).
    """
    itemtypes: list[str] = []
    for section in ("container15", "container26", "project_core", "task_core"):
        for entry in mapping.get(section) or []:
            if entry.get("transform") != "dropdown":
                continue
            itemtype = entry.get("itemtype")
            if itemtype and itemtype not in itemtypes:
                itemtypes.append(itemtype)
    return itemtypes


def run_preflight(
    glpi: GlpiClient,
    redmine: RedmineClient,
    mapping: dict,
    issue_id: int,
) -> bool:
    """Spec section 9.0. Returns False when the migration must not continue.

    The session is already open here (GlpiClient is used as a context manager),
    which is step 1 of the preflight.
    """
    print(messages.PREFLIGHT_HEADER)
    print(messages.PREFLIGHT_SESSION_OK)

    # Step 2 - Fields plugin rights. A missing right is a hard stop: without it
    # we would create projects with silently empty additional fields.
    try:
        glpi.check_fields_plugin_rights()
    except GlpiRightMissingError:
        print(messages.PREFLIGHT_FIELDS_RIGHTS_MISSING)
        return False
    except ApiError as exc:
        print(messages.PREFLIGHT_FIELDS_CHECK_FAILED.format(detail=messages.redact(exc)))
        return False
    print(messages.PREFLIGHT_FIELDS_RIGHTS_OK)

    # Step 2b - the read above only proves READ access to the plugin. Writing a
    # container row on a ProjectTask needs UPDATE on `projecttask` as well, and
    # on 2026-08-07 the profile had it for `project` but not for `projecttask`,
    # so every container-26 row was refused after the tasks had been created.
    # Deliberately a warning, not a stop: only the Faturamento tab is lost, the
    # tasks are created and apply already degrades gracefully. None means the
    # right could not be read - stay silent rather than warn on a guess.
    projecttask_right = glpi.can_write_projecttask_containers()
    if projecttask_right is False:
        print(messages.PREFLIGHT_PROJECTTASK_RIGHT_MISSING)
    elif projecttask_right:
        print(messages.PREFLIGHT_PROJECTTASK_RIGHT_OK)

    # Step 3 - preload and cache every dropdown dictionary.
    itemtypes = dropdown_itemtypes(mapping)
    print(messages.PREFLIGHT_DROPDOWNS_LOADING.format(count=len(itemtypes)))
    empty: list[str] = []
    for itemtype in itemtypes:
        try:
            entries = glpi.load_dropdown(itemtype)
        except ApiError as exc:
            # A missing dictionary is not fatal: the fields depending on it are
            # skipped with a warning (spec 6.3), never guessed.
            print(
                messages.PREFLIGHT_DROPDOWN_FAILED.format(
                    itemtype=itemtype, detail=messages.redact(exc)
                )
            )
            continue
        if entries:
            print(
                messages.PREFLIGHT_DROPDOWN_OK.format(itemtype=itemtype, count=len(entries))
            )
        else:
            # Verified 2026-07-29: 9 of the 12 dictionaries are empty on the
            # live instance. An empty dictionary silently costs every value of
            # that field, so it is surfaced here rather than only per project.
            empty.append(itemtype)
            print(messages.PREFLIGHT_DROPDOWN_EMPTY.format(itemtype=itemtype))
    if empty:
        print(
            messages.PREFLIGHT_DROPDOWNS_EMPTY_SUMMARY.format(
                count=len(empty), total=len(itemtypes), names=", ".join(empty)
            )
        )

    # Step 4 - documents. Both checks WARN and never abort: a project whose
    # files fail to upload is still a correct project, the same reasoning as
    # step 2b. The right is read, not probed with a write, so None ("could not
    # tell") stays silent rather than warning on a guess.
    document_right = glpi.can_create_documents()
    if document_right is False:
        print(messages.PREFLIGHT_DOCUMENT_RIGHT_MISSING)

    try:
        extensions = glpi.load_document_types()
    except ApiError as exc:
        # Only costs the report warning about unknown extensions; the upload
        # itself is unaffected, and GLPI remains the authority on what it takes.
        print(
            messages.PREFLIGHT_DOCUMENT_TYPES_FAILED.format(detail=messages.redact(exc))
        )
    else:
        print(messages.PREFLIGHT_DOCUMENT_TYPES_OK.format(count=len(extensions)))

    # Redmine reachability, checked against the issue actually requested.
    try:
        redmine.fetch_issue(issue_id, include=())
    except ApiError as exc:
        print(messages.PREFLIGHT_REDMINE_FAILED.format(detail=messages.redact(exc)))
        return False
    print(messages.PREFLIGHT_REDMINE_OK.format(issue_id=issue_id))

    print(messages.PREFLIGHT_PASSED)
    return True


def build_project_plan(
    glpi: GlpiClient,
    redmine: RedmineClient,
    mapping: dict,
    issue_id: int,
    skip_attachments: bool = False,
) -> ProjectPlan:
    """Read the whole tree and map it into a GLPI plan. Writes nothing."""
    tree = redmine.fetch_tree(issue_id)
    root_issue = tree.root.issue

    mapper = Mapper(
        mapping=mapping,
        status_resolver=StatusResolver(),
        user_resolver=UserResolver(),
        dropdown_resolver=DropdownResolver(glpi),
    )
    core, container15 = mapper.map_project(root_issue)

    tree_plan = plan_tree(tree.root)
    tasks: list[PlannedTask] = []
    faturamento: list[PlannedFaturamento] = []
    skipped: list[SkippedChild] = []

    for planned in tree_plan.nodes:
        if planned.disposition is Disposition.PROJECT:
            continue
        if planned.disposition is Disposition.TASK:
            tasks.append(
                PlannedTask(
                    issue=planned.node.issue,
                    result=mapper.map_task(planned.node.issue),
                    depth=planned.depth,
                    parent_redmine_id=planned.parent_redmine_id,
                )
            )
        elif planned.disposition is Disposition.FATURAMENTO:
            # A tracker-15 descendant becomes a ProjectTask of type Faturamento
            # on the ROOT project carrying a container-26 row - never a plain
            # task, and never nested under its Redmine parent (spec 6.5, step 2).
            fat_core, fat_container = mapper.map_faturamento(planned.node.issue)
            faturamento.append(
                PlannedFaturamento(
                    issue=planned.node.issue,
                    core=fat_core,
                    result=fat_container,
                    origin="child",
                )
            )
        else:
            skipped.append(
                SkippedChild(
                    issue_id=planned.issue_id,
                    tracker_id=planned.tracker_id,
                    tracker_name=planned.tracker_name,
                    subject=planned.subject,
                    reason=planned.reason,
                )
            )

    # Second Faturamento path: relations of the root issue (spec 6.5, step 1).
    discovery = discover_from_relations(redmine, root_issue)
    for related_issue in discovery.issues:
        fat_core, fat_container = mapper.map_faturamento(related_issue)
        faturamento.append(
            PlannedFaturamento(
                issue=related_issue,
                core=fat_core,
                result=fat_container,
                origin="relation",
            )
        )

    # Attachments last: the host of every file is the disposition assigned
    # above, so this needs the finished tree plan and the relation discovery.
    # Only the RELATION-sourced Faturamento issues are passed - a tracker-15
    # descendant is already a node of tree_plan and would be counted twice.
    attachments = plan_attachments(
        tree_plan,
        faturamento_issues=discovery.issues,
        root_issue_id=int(root_issue["id"]),
        tree_failures=[*tree.failures, *discovery.failures],
        document_types=glpi.document_type_extensions(),
        skip=skip_attachments,
    )

    return ProjectPlan(
        issue=root_issue,
        core=core,
        container15=container15,
        never_write=mapper.never_write_records(),
        tasks=tasks,
        faturamento=faturamento,
        skipped_children=skipped,
        attachments=attachments,
        ignored_relations=discovery.ignored_relations,
        tree_failures=[*tree.failures, *discovery.failures],
        tree_cycles=tree.cycles,
        tree_nodes=tree_plan.nodes,
    )


def check_already_migrated(glpi: GlpiClient, issue_id: int) -> int | None:
    """Spec 9.1: the rdmfield marker in GLPI is the authoritative dedup check.

    Redmine and GLPI ids are independent, so the marker - not the id - is what
    tells us a project was already migrated.
    """
    rows = glpi.find_by_rdmfield(issue_id)
    if not rows:
        return None
    return int(rows[0].get("items_id") or 0) or None


def project_create_payload(plan: ProjectPlan) -> dict:
    """Input for POST /Project: the core columns AND the container-15 values.

    Container 15 is a Fields-plugin container of type "dom", i.e. its fields
    live on the Project's own form. The plugin therefore validates its
    mandatory columns inside the Project's add hook, reading them from the
    Project input - not from the container row we write afterwards. Posting the
    core columns alone makes GLPI reject the project itself with
        ERROR_GLPI_ADD "Alguns campos obrigatórios estão vazios :
        Gestão, Responsável Cliente, Despesa, Complexidade, Valor do Projeto"
    even when the plan holds all five values (verified live 2026-08-04: fields
    161, 162, 163, 164 and 189 of container 15 carry mandatory=1; they were not
    flagged mandatory when the first projects were migrated on 2026-07-30).

    Sending every container-15 value, not just the five mandatory ones, keeps
    this working if someone flags another column mandatory later. The plugin
    creates the container row from these values; write_additional_fields_row
    then finds that row and updates it, which is what fills in the columns the
    plugin skips - rdmfield, the dedup marker, among them (it is is_active 0).
    """
    payload = dict(plan.container15.payload)
    # Core columns win on any name clash: they are the ones GLPI's own Project
    # table understands.
    payload.update(plan.core.payload)
    return payload


def apply_plan(
    glpi: GlpiClient,
    plan: ProjectPlan,
    store: MigrationStore,
    redmine: RedmineClient | None = None,
) -> bool:
    """Write the plan to GLPI. Returns False when a hard step failed.

    Order is fixed by spec 9.2: project, then the container-15 row (always
    carrying rdmfield, even if every other field is empty), then the tasks
    parent-before-child, then the Faturamento tasks with their container-26
    rows (revised 2026-08-06 - these used to be container-25 rows on the
    project). Step 5, the attachments, was added 2026-08-10 and comes last
    because every host item must already exist.

    `redmine` is optional so existing callers and tests that never reach step 5
    keep working; without it the attachments are left untouched, since the files
    can only come from Redmine.
    """
    print()
    print(messages.APPLY_HEADER)

    # 1. Project - carries the container-15 values too, see the docstring above.
    project_id = glpi.create_project(project_create_payload(plan))
    plan.glpi_project_id = project_id
    plan.glpi_ids[plan.issue_id] = project_id
    store.record(plan.issue_id, project_id, "Project", status=STATUS_OK)
    print(messages.APPLY_PROJECT_CREATED.format(glpi_id=project_id, issue_id=plan.issue_id))

    # 2. Container 15 - rdmfield is the dedup marker and must always be written.
    row_id = glpi.write_additional_fields_row(project_id, plan.container15.payload)
    print(messages.APPLY_CONTAINER15_WRITTEN.format(row_id=row_id))

    # 3. Tasks, parent before child. projects_id always points at the root
    #    project; projecttasks_id comes from the parent's entry in the map.
    for task in plan.tasks:
        existing = store.lookup(task.issue_id, "ProjectTask")
        if existing:
            plan.glpi_ids[task.issue_id] = existing.glpi_id
            print(
                messages.DEDUP_LOCAL_HIT.format(
                    issue_id=task.issue_id,
                    itemtype="ProjectTask",
                    glpi_id=existing.glpi_id,
                )
            )
            continue

        payload = dict(task.result.payload)
        payload["projects_id"] = project_id
        parent_glpi_id = plan.glpi_ids.get(task.parent_redmine_id)
        if parent_glpi_id and task.parent_redmine_id != plan.issue_id:
            payload["projecttasks_id"] = parent_glpi_id

        task_id = glpi.create_project_task(payload)
        task.glpi_id = task_id
        plan.glpi_ids[task.issue_id] = task_id
        store.record(
            task.issue_id,
            task_id,
            "ProjectTask",
            parent_redmine_id=task.parent_redmine_id,
            status=STATUS_OK,
        )
        print(messages.APPLY_TASK_CREATED.format(glpi_id=task_id, issue_id=task.issue_id))

    # 4. Faturamento: one ProjectTask of type Faturamento per tracker-15 issue,
    #    then its container-26 row. The task always hangs off the ROOT project
    #    with no projecttasks_id - a Faturamento is never nested under its
    #    Redmine parent, which is also why a skipped ancestor does not orphan it.
    #
    #    The task is the load-bearing write; the container row is not. If GLPI
    #    rejects the row, keep the task, report the values in full and carry on
    #    (spec 6.5) - aborting here would leave a project half written.
    for item in plan.faturamento:
        existing = store.lookup(item.issue_id, "ProjectTask")
        if existing:
            item.glpi_task_id = existing.glpi_id
        else:  # noqa: RET505 - the id is registered in plan.glpi_ids below
            task_payload = dict(item.core.payload)
            task_payload["projects_id"] = project_id
            item.glpi_task_id = glpi.create_project_task(task_payload)
            store.record(
                item.issue_id, item.glpi_task_id, "ProjectTask", status=STATUS_OK
            )
            print(
                messages.APPLY_FATURAMENTO_TASK_CREATED.format(
                    glpi_id=item.glpi_task_id, issue_id=item.issue_id
                )
            )

        # Faturamento task ids used to live only on the item, so nothing else
        # could find them. Registering them here is what lets an attachment
        # resolve its host in step 5, and it also fills the GLPI column of the
        # tree in the report - _tree_lines reads exactly this map.
        plan.glpi_ids[item.issue_id] = item.glpi_task_id

        try:
            row = glpi.create_faturamento_row(item.glpi_task_id, item.result.payload)
        except ApiError as exc:
            item.written = False
            note = messages.APPLY_FATURAMENTO_DEGRADED.format(
                issue_id=item.issue_id,
                task_id=item.glpi_task_id,
                detail=messages.redact(exc),
            )
            print(note)
            plan.notes.append(note)
            continue
        item.glpi_id = row
        store.record(item.issue_id, row, ITEMTYPE_FATURAMENTO, status=STATUS_OK)
        print(
            messages.APPLY_FATURAMENTO_CREATED.format(
                row_id=row, task_id=item.glpi_task_id, issue_id=item.issue_id
            )
        )

    # 5. Attachments -> GLPI Documents. Last, because every host must exist.
    if redmine is not None:
        apply_attachments(glpi, redmine, plan, store)

    print(messages.APPLY_DONE)
    return True


def apply_attachments(
    glpi: GlpiClient,
    redmine: RedmineClient,
    plan: ProjectPlan,
    store: MigrationStore,
) -> None:
    """Upload each planned attachment and link it to its host item.

    Degrades, never aborts. By the time this runs the project, its tasks and
    its Faturamento rows are already written; raising here would leave a
    half-migrated project behind for the sake of one file. Every failure sets an
    outcome on the PlannedAttachment, adds a note to the report and moves on -
    the same rule the container-26 row follows in step 4.
    """
    pending = [
        item
        for item in plan.attachments
        if item.outcome is AttachmentOutcome.PLANNED and item.host_itemtype
    ]
    if not pending:
        return

    print()
    print(messages.APPLY_ATTACHMENTS_HEADER.format(count=len(pending)))

    for item in pending:
        host_id = plan.glpi_ids.get(item.host_redmine_id)
        if not host_id:
            # The host was planned but never created (a task whose POST failed,
            # or a Faturamento that degraded). Not a file problem - say so.
            item.outcome = AttachmentOutcome.NO_HOST
            item.detail = messages.REPORT_ATTACHMENT_HOST_MISSING
            note = messages.APPLY_ATTACHMENT_NO_HOST.format(
                attachment_id=item.attachment_id,
                issue_id=item.issue_id,
                filename=item.filename,
            )
            print(note)
            plan.notes.append(note)
            continue

        try:
            _migrate_one_attachment(glpi, redmine, item, host_id, store)
        except ApiError as exc:
            # The outcome was already set by the helper where it knew which
            # step failed; this is the catch-all for anything it did not.
            if item.outcome is AttachmentOutcome.PLANNED:
                item.outcome = AttachmentOutcome.FAILED_UPLOAD
            item.detail = messages.redact(exc)
            note = messages.APPLY_ATTACHMENT_FAILED.format(
                attachment_id=item.attachment_id,
                issue_id=item.issue_id,
                filename=item.filename,
                detail=messages.redact(exc),
            )
            print(note)
            plan.notes.append(note)


def _migrate_one_attachment(
    glpi: GlpiClient,
    redmine: RedmineClient,
    item,
    host_id: int,
    store: MigrationStore,
) -> None:
    """One attachment: dedup, download, upload, link, record.

    GLPI is asked first and the local map second - deliberately the same order
    as the project's own dedup (spec 9.1). The marker in the document's comment
    survives a lost migration.db; the SQLite row is a crash guard, not the
    authority.
    """
    existing = glpi.find_document_by_marker(item.attachment_id)
    if existing:
        document_id = int(existing[0]["id"])
        item.glpi_document_id = document_id
        item.outcome = AttachmentOutcome.DEDUP_GLPI
        _ensure_link(glpi, document_id, item.host_itemtype, host_id)
        store.record(
            item.attachment_id,
            document_id,
            ITEMTYPE_DOCUMENT,
            parent_redmine_id=item.issue_id,
            status=STATUS_OK,
        )
        print(
            messages.APPLY_ATTACHMENT_DEDUP.format(
                attachment_id=item.attachment_id,
                issue_id=item.issue_id,
                glpi_id=document_id,
            )
        )
        return

    local = store.lookup(item.attachment_id, ITEMTYPE_DOCUMENT)
    if local:
        # The marker is gone from GLPI but the map still points at a document.
        # Trust it only if the document is really there; otherwise fall through
        # and upload again rather than silently losing the file.
        if glpi.get_item(ITEMTYPE_DOCUMENT, local.glpi_id):
            item.glpi_document_id = local.glpi_id
            item.outcome = AttachmentOutcome.DEDUP_LOCAL
            _ensure_link(glpi, local.glpi_id, item.host_itemtype, host_id)
            return

    with tempfile.TemporaryDirectory(prefix="rdm-anexo-") as workdir:
        # Redmine filenames carry accents, spaces and slashes-in-subject; only
        # the basename is used and the directory is thrown away either way.
        local_path = Path(workdir) / (Path(item.filename).name or "anexo")
        try:
            redmine.download_attachment(item.content_url, local_path)
        except ApiError:
            item.outcome = AttachmentOutcome.FAILED_DOWNLOAD
            raise

        try:
            document_id = glpi.upload_document(
                local_path,
                name=item.filename,
                comment=_document_comment(item),
            )
        except ApiError:
            item.outcome = AttachmentOutcome.FAILED_UPLOAD
            raise

    item.glpi_document_id = document_id
    store.record(
        item.attachment_id,
        document_id,
        ITEMTYPE_DOCUMENT,
        parent_redmine_id=item.issue_id,
        status=STATUS_OK,
    )

    try:
        _ensure_link(glpi, document_id, item.host_itemtype, host_id)
    except ApiError:
        # The document exists and is recorded; only the link is missing, and
        # that is a different repair than a re-upload. Say which one it is.
        item.outcome = AttachmentOutcome.FAILED_LINK
        raise

    item.outcome = AttachmentOutcome.UPLOADED
    print(
        messages.APPLY_ATTACHMENT_UPLOADED.format(
            attachment_id=item.attachment_id,
            issue_id=item.issue_id,
            glpi_id=document_id,
            itemtype=item.host_itemtype,
            items_id=host_id,
            filename=item.filename,
        )
    )


def _ensure_link(glpi: GlpiClient, document_id: int, itemtype: str, items_id: int) -> None:
    """Link the document to the item unless that link already exists.

    Checked rather than blindly posted: GLPI refuses a duplicate Document_Item,
    and that refusal would otherwise read as a real failure on a re-run.
    """
    for row in glpi.document_links(itemtype, items_id):
        if str(row.get("documents_id")) == str(int(document_id)):
            return
    glpi.link_document(document_id, itemtype, items_id)


def _document_comment(item) -> str:
    """Document.comment: the dedup marker first, then the Redmine provenance.

    The marker MUST stay on its own first line - find_document_by_marker
    compares whole lines so that rdmattachment:2931 cannot match
    rdmattachment:29314.
    """
    lines = [
        f"{DOCUMENT_MARKER_PREFIX}{item.attachment_id}",
        messages.DOCUMENT_ORIGIN.format(
            issue_id=item.issue_id, attachment_id=item.attachment_id
        ),
    ]
    if item.description:
        lines.append(messages.DOCUMENT_DESCRIPTION.format(text=item.description))
    if item.author or item.created_on:
        lines.append(
            messages.DOCUMENT_AUTHOR.format(
                author=item.author or "?", created_on=item.created_on or "?"
            )
        )
    return "\n".join(lines)


def confirm_apply() -> bool:
    """Explicit confirmation, required after the plan report (spec 9.2)."""
    try:
        answer = input(messages.APPLY_CONFIRM_PROMPT)
    except EOFError:
        return False
    return answer.strip().casefold() in messages.APPLY_CONFIRM_ACCEPT


def main(argv: list[str] | None = None) -> int:
    # PT-BR text is accented; force UTF-8 so Windows consoles and redirected
    # output render it correctly.
    # line_buffering keeps stdout and stderr interleaved in the order they were
    # written; without it a buffered stdout can surface after an unbuffered
    # stderr message and make the output read out of sequence.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    args = build_parser().parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        detail = str(exc)
        if detail == "REDMINE_URL":
            print(messages.CONFIG_REDMINE_URL_INVALID, file=sys.stderr)
        else:
            print(messages.CONFIG_MISSING_VARS.format(names=detail), file=sys.stderr)
        return EXIT_CONFIG

    # Register every token so redact() can scrub it from any output.
    messages.register_secrets(settings.secret_values())

    try:
        mapping = load_yaml("mapping.yml")
    except ConfigError as exc:
        print(messages.CONFIG_FILE_UNREADABLE.format(path=exc), file=sys.stderr)
        return EXIT_CONFIG

    print(messages.CLI_MODE_APPLY if args.apply else messages.CLI_MODE_DRY_RUN)
    print()

    try:
        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi, RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
            if not run_preflight(glpi, redmine, mapping, args.issue):
                print(messages.PREFLIGHT_ABORTED, file=sys.stderr)
                return EXIT_FAILED

            # Deduplication before anything else (spec 9.1). v1 does not
            # update projects that already exist.
            existing_project = check_already_migrated(glpi, args.issue)
            if existing_project:
                print(
                    messages.DEDUP_ALREADY_MIGRATED.format(
                        issue_id=args.issue, glpi_id=existing_project
                    ),
                    file=sys.stderr,
                )
                return EXIT_FAILED

            plan = build_project_plan(
                glpi, redmine, mapping, args.issue, args.skip_attachments
            )

            print()
            print(Reporter(plan, apply_mode=args.apply).render())

            if args.apply:
                if not (args.yes or confirm_apply()):
                    print(messages.APPLY_CANCELLED)
                    return EXIT_OK
                with MigrationStore(args.db) as store:
                    apply_plan(glpi, plan, store, redmine=redmine)
                # Re-render so the report shows the Redmine -> GLPI ids.
                print()
                print(Reporter(plan, apply_mode=True).render())

            if args.report:
                saved = Reporter(plan, apply_mode=args.apply).save(args.report)
                print()
                print(messages.REPORT_SAVED.format(path=saved))

            return EXIT_OK
    except ApiError as exc:
        print(messages.PREFLIGHT_SESSION_FAILED.format(detail=messages.redact(exc)),
              file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())

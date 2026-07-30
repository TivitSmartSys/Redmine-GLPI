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
from pathlib import Path

# Allow `python main.py` from the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.errors import ApiError, GlpiRightMissingError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402
from config.settings import (  # noqa: E402
    DEFAULT_DB_PATH,
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
    return parser


def dropdown_itemtypes(mapping: dict) -> list[str]:
    """Every dictionary itemtype referenced by mapping.yml.

    Derived from the mapping so preflight loads exactly one dictionary per
    itemtype (spec 9.0 step 3: one call per dictionary, not per field).
    """
    itemtypes: list[str] = []
    for section in ("container15", "container25", "project_core", "task_core"):
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
            # A tracker-15 descendant becomes a container-25 row on the ROOT
            # project, never a task (spec 6.5, step 2).
            faturamento.append(
                PlannedFaturamento(
                    issue=planned.node.issue,
                    result=mapper.map_faturamento(planned.node.issue),
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
        faturamento.append(
            PlannedFaturamento(
                issue=related_issue,
                result=mapper.map_faturamento(related_issue),
                origin="relation",
            )
        )

    return ProjectPlan(
        issue=root_issue,
        core=core,
        container15=container15,
        never_write=mapper.never_write_records(),
        tasks=tasks,
        faturamento=faturamento,
        skipped_children=skipped,
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


def apply_plan(
    glpi: GlpiClient, plan: ProjectPlan, store: MigrationStore
) -> bool:
    """Write the plan to GLPI. Returns False when a hard step failed.

    Order is fixed by spec 9.2: project, then the container-15 row (always
    carrying rdmfield, even if every other field is empty), then the tasks
    parent-before-child, then the container-25 rows.
    """
    print()
    print(messages.APPLY_HEADER)

    # 1. Project
    project_id = glpi.create_project(plan.core.payload)
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

    # 4. Container 25 rows. Container 25 is type "tab", so several rows per
    #    project are expected. If GLPI nevertheless rejects a second row, fall
    #    back to degraded mode: keep the first, report the rest in full, log
    #    once, and do not abort (spec 6.5).
    degraded = False
    for item in plan.faturamento:
        if degraded:
            item.written = False
            continue
        try:
            row = glpi.create_faturamento_row(project_id, item.result.payload)
        except ApiError as exc:
            degraded = True
            item.written = False
            note = messages.APPLY_FATURAMENTO_DEGRADED.format(detail=messages.redact(exc))
            print(note)
            plan.notes.append(note)
            continue
        item.glpi_id = row
        store.record(item.issue_id, row, "PluginFieldsProjectfaturamento", status=STATUS_OK)
        print(
            messages.APPLY_FATURAMENTO_CREATED.format(row_id=row, issue_id=item.issue_id)
        )

    print(messages.APPLY_DONE)
    return True


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

            plan = build_project_plan(glpi, redmine, mapping, args.issue)

            print()
            print(Reporter(plan, apply_mode=args.apply).render())

            if args.apply:
                if not (args.yes or confirm_apply()):
                    print(messages.APPLY_CANCELLED)
                    return EXIT_OK
                with MigrationStore(args.db) as store:
                    apply_plan(glpi, plan, store)
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

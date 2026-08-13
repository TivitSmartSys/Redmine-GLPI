"""Forget one migration so the same issue can be migrated again.

There are two independent caches, and clearing only one is worse than clearing
neither:

  * the `rdmfield` marker on the container-15 row in GLPI. This is what
    `check_already_migrated` reads (spec 9.1) and the only thing that actually
    refuses a re-run - in dry-run too, before the plan is even built. A plugin
    container row outlives the project it hangs off, so deleting the project in
    the GLPI UI does NOT remove the marker.
  * the rows in `migration_map` (SQLite). They do not block anything, but
    `apply_plan` consults them per task: left behind, they make the next run
    skip every task and reuse GLPI ids that no longer exist, producing a project
    with no tasks at all.

Policy: the marker is deleted only when it is orphaned - the project it points
at is gone, or sits in the GLPI trash. While the project is alive the marker is
doing its job and this tool refuses to touch anything.

Default mode is a diagnosis that writes nothing, mirroring main.py: --apply plus
a typed confirmation are both required.
"""

from __future__ import annotations

import argparse
import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

# Allow `python reset_migration.py` from the project root without installing.
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
from store.db import MigrationStore  # noqa: E402
from transform.attachments import attachments_of  # noqa: E402
from transform.notes import journals_of  # noqa: E402
from transform.faturamento import discover_from_relations  # noqa: E402
from transform.tree import plan_tree  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

# Marker states. ORPHAN and TRASHED may be deleted; ACTIVE stops the run.
ORPHAN = "orphan"
TRASHED = "trashed"
ACTIVE = "active"


@dataclass(frozen=True)
class Marker:
    """One container-15 row carrying the rdmfield of the issue being reset."""

    row_id: int
    project_id: int | None
    state: str

    @property
    def deletable(self) -> bool:
        return self.state in (ORPHAN, TRASHED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reset_migration.py",
        description=messages.RESET_DESCRIPTION,
    )
    parser.add_argument("--issue", type=int, required=True, help=messages.CLI_HELP_RESET_ISSUE)
    parser.add_argument("--apply", action="store_true", help=messages.CLI_HELP_RESET_APPLY)
    parser.add_argument("--yes", action="store_true", help=messages.CLI_HELP_RESET_YES)
    parser.add_argument(
        "--local-only", action="store_true", help=messages.CLI_HELP_RESET_LOCAL_ONLY
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=messages.CLI_HELP_DB)
    return parser


def collect_tree_ids(redmine: RedmineClient, issue_id: int) -> list[int]:
    """Every Redmine id the migration of `issue_id` could have recorded.

    Read from Redmine rather than from the map: `migration_map` has no root
    column and Faturamento rows are stored with `parent_redmine_id = NULL`, so
    the tree cannot be reconstructed locally.

    Out-of-scope nodes are included on purpose. They have no rows today, so
    listing them costs nothing, and it keeps the reset complete if the scope
    ever widens.

    ATTACHMENT ids and JOURNAL ids are collected too. A Document row stores the
    id of the *attachment* and a Notepad row the id of the *journal*, not of the
    issue, so a reset that only listed issue ids would leave every document and
    every note row behind - and the next run would then trust a map pointing at
    rows it can no longer explain. The three counters are independent, but they
    never collide in the table: the primary key is (redmine_id, glpi_itemtype)
    and each of Document and Notepad uses its own itemtype.

    The GLPI documents themselves are deliberately NOT deleted here. Their
    `rdmattachment:` marker survives, so a re-run finds them and simply re-links
    them to the newly created items instead of uploading 55 MB again.

    Notepad rows need no such care, and that is the one asymmetry worth naming:
    a note hangs off its host item and is destroyed with it, so once the project
    is gone there is nothing left in GLPI to clean up - only the local map.
    """
    tree = redmine.fetch_tree(issue_id)
    ids: list[int] = []
    for planned in plan_tree(tree.root).nodes:
        ids.append(planned.issue_id)
        ids.extend(_attachment_ids(planned.node.issue))
        ids.extend(_journal_ids(planned.node.issue))
    # Second Faturamento path: tracker-15 partners found through relations, which
    # are not part of the tree walk (spec 6.5, step 1).
    for related in discover_from_relations(redmine, tree.root.issue).issues:
        ids.append(int(related["id"]))
        ids.extend(_attachment_ids(related))
        ids.extend(_journal_ids(related))
    return ids


def _attachment_ids(issue: dict) -> list[int]:
    return [
        int(item["id"])
        for item in attachments_of(issue)
        if item.get("id") is not None
    ]


def _journal_ids(issue: dict) -> list[int]:
    """Every journal id, text or not.

    History-only entries never produced a Notepad row, so listing them finds
    nothing - which is cheaper than deciding here which entries the migration
    considered worth writing, a rule that lives in transform.notes and may
    change without this file noticing.
    """
    return [
        int(item["id"])
        for item in journals_of(issue)
        if item.get("id") is not None
    ]


def classify_marker(glpi: GlpiClient, row: dict) -> Marker:
    """Decide whether a container-15 row is still backed by a live project."""
    row_id = int(row.get("id") or 0)
    try:
        project_id = int(row.get("items_id") or 0)
    except (TypeError, ValueError):
        project_id = 0

    if not project_id:
        return Marker(row_id=row_id, project_id=None, state=ORPHAN)

    project = glpi.get_item("Project", project_id)
    if project is None:
        return Marker(row_id=row_id, project_id=project_id, state=ORPHAN)
    if str(project.get("is_deleted") or "0") not in ("0", "False"):
        return Marker(row_id=row_id, project_id=project_id, state=TRASHED)
    return Marker(row_id=row_id, project_id=project_id, state=ACTIVE)


def describe_marker(marker: Marker) -> str:
    if marker.state == ORPHAN:
        return messages.RESET_MARKER_ORPHAN.format(
            row_id=marker.row_id, project_id=marker.project_id
        )
    if marker.state == TRASHED:
        return messages.RESET_MARKER_TRASHED.format(
            row_id=marker.row_id, project_id=marker.project_id
        )
    return messages.RESET_MARKER_ACTIVE.format(
        row_id=marker.row_id, project_id=marker.project_id
    )


def confirm_reset() -> bool:
    """Same gate as the migration: an explicit word, after the full diagnosis."""
    try:
        answer = input(messages.RESET_CONFIRM_PROMPT)
    except EOFError:
        return False
    return answer.strip().casefold() in messages.APPLY_CONFIRM_ACCEPT


def main(argv: list[str] | None = None) -> int:
    # PT-BR text is accented; force UTF-8 so Windows consoles render it.
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

    # Register every token so redact() can scrub it from any output (rule 5).
    messages.register_secrets(settings.secret_values())

    print(messages.RESET_HEADER.format(issue_id=args.issue))
    print(messages.RESET_MODE_APPLY if args.apply else messages.RESET_MODE_DRY)
    if args.local_only:
        print(messages.RESET_MODE_LOCAL_ONLY)
    print()

    try:
        with ExitStack() as stack:
            redmine = stack.enter_context(
                RedmineClient(settings.redmine_url, settings.redmine_api_key)
            )
            # --local-only never opens a GLPI session at all.
            glpi = (
                None
                if args.local_only
                else stack.enter_context(
                    GlpiClient(
                        settings.glpi_url,
                        settings.glpi_user_token,
                        settings.glpi_app_token,
                    )
                )
            )

            # Since 2026-08-12 a project lives in the entity of its Cliente, so
            # this script has to look at the whole tree for exactly the reason
            # main.py does: a session left in its own branch cannot see - and
            # therefore cannot clean up - anything filed elsewhere. Without it
            # the diagnosis would read "nothing to reset" for a project that is
            # very much there.
            if glpi is not None:
                glpi.set_active_entity_root()

            try:
                tree_ids = collect_tree_ids(redmine, args.issue)
            except ApiError as exc:
                print(
                    messages.RESET_TREE_FAILED.format(
                        issue_id=args.issue, detail=messages.redact(exc)
                    ),
                    file=sys.stderr,
                )
                return EXIT_FAILED
            print(messages.RESET_TREE_SCANNED.format(count=len(tree_ids)))
            print()

            store = stack.enter_context(MigrationStore(args.db))
            local_rows = store.entries_for_ids(tree_ids)
            print(messages.RESET_LOCAL_HEADER.format(path=args.db))
            for entry in local_rows:
                print(
                    messages.RESET_LOCAL_ROW.format(
                        issue_id=entry.redmine_id,
                        itemtype=entry.glpi_itemtype,
                        glpi_id=entry.glpi_id,
                        migrated_at=entry.migrated_at,
                    )
                )
            if local_rows:
                print(messages.RESET_LOCAL_COUNT.format(count=len(local_rows)))
            else:
                print(messages.RESET_LOCAL_EMPTY)
            print()

            markers: list[Marker] = []
            if glpi is not None:
                print(messages.RESET_MARKER_HEADER)
                markers = [
                    classify_marker(glpi, row) for row in glpi.find_by_rdmfield(args.issue)
                ]
                for marker in markers:
                    print(describe_marker(marker))
                if not markers:
                    print(messages.RESET_MARKER_NONE)
                print()

            # A live project means the marker is not stale. Refuse before asking
            # for a confirmation the user should not be given in the first place.
            if any(marker.state == ACTIVE for marker in markers):
                print(messages.RESET_REFUSED_ACTIVE, file=sys.stderr)
                return EXIT_FAILED

            deletable = [marker for marker in markers if marker.deletable]
            if not deletable and not local_rows:
                print(messages.RESET_NOTHING_TO_DO)
                return EXIT_OK

            if not args.apply:
                return EXIT_OK

            if not (args.yes or confirm_reset()):
                print(messages.RESET_CANCELLED)
                return EXIT_OK
            print()

            # GLPI first: it is the side that blocks. Failing after the local map
            # was already cleared would leave the worst state of the two - the
            # migration still refused, with nothing left to explain why.
            for marker in deletable:
                try:
                    glpi.delete_item(ITEMTYPE_ADDITIONAL_FIELDS, marker.row_id)
                except ApiError as exc:
                    print(
                        messages.RESET_MARKER_DELETE_FAILED.format(
                            row_id=marker.row_id, detail=messages.redact(exc)
                        ),
                        file=sys.stderr,
                    )
                    return EXIT_FAILED
                print(
                    messages.RESET_MARKER_DELETED.format(
                        itemtype=ITEMTYPE_ADDITIONAL_FIELDS, row_id=marker.row_id
                    )
                )

            try:
                removed = store.delete_for_ids(tree_ids)
            except Exception as exc:  # sqlite3.Error, or a locked file
                print(
                    messages.RESET_LOCAL_DELETE_FAILED.format(
                        detail=messages.redact(exc)
                    ),
                    file=sys.stderr,
                )
                return EXIT_FAILED
            print(messages.RESET_LOCAL_DELETED.format(count=removed))

            # Prove it: the marker search is exactly what the next run will do.
            if glpi is not None and glpi.find_by_rdmfield(args.issue):
                print(
                    messages.RESET_VERIFY_FAILED.format(issue_id=args.issue),
                    file=sys.stderr,
                )
                return EXIT_FAILED

            print()
            print(messages.RESET_VERIFY_OK.format(issue_id=args.issue))
            return EXIT_OK
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())

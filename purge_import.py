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

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from clients.errors import ApiError
from clients.glpi import GlpiClient
from config.settings import ConfigError, ITEMTYPE_ADDITIONAL_FIELDS, load_settings
from report import messages

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
    """PT-BR plan report. `applied` decides which of the two headers is used -
    the saved file is evidence and must say whether it is a preview or a
    record of what already happened."""
    header = messages.PURGE_HEADER_APPLIED if applied else messages.PURGE_HEADER_PLANNED
    lines = [header, "=" * len(header), ""]
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
    """Same gate as main.py's confirm_apply and reset_migration.py's
    confirm_reset: an explicit word, after the full report."""
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


@dataclass
class PurgeCounts:
    projects: int = 0
    containers: int = 0
    notes: int = 0
    links: int = 0
    failed: int = 0

    def add(self, other: "PurgeCounts") -> None:
        self.projects += other.projects
        self.containers += other.containers
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
                notes=totals.notes, links=totals.links, failed=totals.failed,
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

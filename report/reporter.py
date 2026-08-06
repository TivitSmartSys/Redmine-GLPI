"""Migration report (spec section 10).

The report is the primary functional requirement, not a side effect: every field
that does not reach GLPI must be visible here, with its Redmine name and value.
Section 7 closes the loop arithmetically - if the categories do not add up to the
number of source fields analysed, the migrator itself is broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from config.settings import MANDATORY_CONTAINER26_COLUMNS
from report import messages
from transform.mapper import FieldRecord, MappingResult, Outcome

WIDTH = 80
RULE = "=" * WIDTH
THIN = "-" * WIDTH


@dataclass
class PlannedTask:
    """One descendant that becomes a GLPI ProjectTask."""

    issue: dict
    result: MappingResult
    depth: int
    parent_redmine_id: int | None
    glpi_id: int | None = None

    @property
    def issue_id(self) -> int:
        return int(self.issue["id"])


@dataclass
class PlannedFaturamento:
    """One tracker-15 issue that becomes a typed ProjectTask + container-26 row.

    Two payloads, two GLPI ids: `core`/`glpi_task_id` for the ProjectTask of
    type Faturamento, `result`/`glpi_id` for the container-26 row hanging off
    it. Before 2026-08-06 this was a single container-25 row on the Project,
    which is why the container payload keeps the plain name `result`.
    """

    issue: dict
    result: MappingResult          # container-26 columns
    core: MappingResult | None = None  # ProjectTask columns
    origin: str = "relation"  # 'relation' | 'child'
    glpi_task_id: int | None = None
    glpi_id: int | None = None
    written: bool = True  # False when degraded to report-only

    @property
    def issue_id(self) -> int:
        return int(self.issue["id"])


@dataclass
class SkippedChild:
    issue_id: int
    tracker_id: int | None
    tracker_name: str
    subject: str
    reason: str


@dataclass
class ProjectPlan:
    """Everything the migration intends to do for one root issue."""

    issue: dict
    core: MappingResult
    container15: MappingResult
    never_write: list[FieldRecord] = field(default_factory=list)
    tasks: list[PlannedTask] = field(default_factory=list)
    faturamento: list[PlannedFaturamento] = field(default_factory=list)
    skipped_children: list[SkippedChild] = field(default_factory=list)
    ignored_relations: list = field(default_factory=list)
    tree_failures: list = field(default_factory=list)
    tree_cycles: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    glpi_project_id: int | None = None
    # PlannedNode list from transform.tree, in pre-order.
    tree_nodes: list = field(default_factory=list)
    # redmine_id -> GLPI id, filled during --apply so the report can show the
    # Redmine -> GLPI correspondence required by spec section 10.
    glpi_ids: dict = field(default_factory=dict)

    @property
    def issue_id(self) -> int:
        return int(self.issue["id"])

    def all_records(self) -> list[FieldRecord]:
        """Every field record from the project, its tasks and its invoices.

        The integrity check counts these, so a task field cannot escape it.
        """
        records = [*self.core.records, *self.container15.records]
        for task in self.tasks:
            records.extend(task.result.records)
        for item in self.faturamento:
            if item.core is not None:
                records.extend(item.core.records)
            records.extend(item.result.records)
        return records


class Reporter:
    def __init__(self, plan: ProjectPlan, apply_mode: bool = False):
        self._plan = plan
        self._apply = apply_mode
        self._lines: list[str] = []

    # -- rendering helpers -------------------------------------------------

    def _add(self, text: str = "") -> None:
        self._lines.append(text)

    def _section(self, title: str) -> None:
        self._add()
        self._add(title)
        self._add(THIN)

    def _records(self, records: list[FieldRecord], show_value: bool = True) -> None:
        if not records:
            self._add(messages.REPORT_NOTHING)
            return
        for record in sorted(
            records, key=lambda item: (item.origin, item.source_label.casefold())
        ):
            parts = [f"  - {record.source_label}"]
            if show_value and record.raw_value:
                parts.append(f' = "{record.raw_value}"')
            if record.origin:
                parts.append(f" [{record.origin}]")
            if record.detail:
                parts.append(f" — {record.detail}")
            self._add("".join(parts))

    # -- sections ----------------------------------------------------------

    def _header(self) -> None:
        issue = self._plan.issue
        self._add(RULE)
        self._add(messages.REPORT_TITLE.format(issue_id=self._plan.issue_id))
        self._add(messages.REPORT_MODE_APPLY if self._apply else messages.REPORT_MODE_DRY_RUN)
        self._add(
            messages.REPORT_GENERATED_AT.format(
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        self._add(RULE)

    def _section_what(self) -> None:
        plan = self._plan
        issue = plan.issue
        tracker = issue.get("tracker") or {}
        self._section(messages.REPORT_SECTION_1)
        self._add(
            messages.REPORT_PROJECT_LINE.format(name=issue.get("subject") or "")
        )
        self._add(
            messages.REPORT_ORIGIN_LINE.format(
                issue_id=plan.issue_id,
                tracker_id=tracker.get("id"),
                tracker_name=tracker.get("name") or "",
            )
        )
        self._add(messages.REPORT_TASKS_LINE.format(count=len(plan.tasks)))
        self._add(messages.REPORT_FATURAMENTO_LINE.format(count=len(plan.faturamento)))

        if plan.tree_failures:
            self._add(
                messages.REPORT_TREE_FAILURES.format(
                    count=len(plan.tree_failures),
                    ids=", ".join(str(item[0]) for item in plan.tree_failures),
                )
            )
        if plan.tree_cycles:
            self._add(
                messages.REPORT_TREE_CYCLES.format(
                    ids=", ".join(str(item) for item in plan.tree_cycles)
                )
            )

        if plan.tree_nodes:
            self._add()
            self._add(messages.REPORT_TREE_HEADER)
            self._tree_lines()

        self._add()
        self._add(messages.REPORT_CORE_HEADER)
        self._payload_lines(plan.core)
        self._add()
        self._add(messages.REPORT_CONTAINER15_HEADER)
        self._payload_lines(plan.container15)

        if plan.tasks:
            self._add()
            self._add("  " + messages.REPORT_SECTION_TASKS)
            for task in plan.tasks:
                self._add()
                self._add(
                    messages.REPORT_TASK_ITEM.format(
                        issue_id=task.issue_id,
                        subject=task.issue.get("subject") or "",
                    )
                )
                self._payload_lines(task.result, indent=4)

        if plan.faturamento:
            self._add()
            self._add("  " + messages.REPORT_SECTION_FATURAMENTO)
            for item in plan.faturamento:
                self._add()
                self._add(
                    messages.REPORT_FATURAMENTO_ITEM.format(
                        issue_id=item.issue_id,
                        subject=item.issue.get("subject") or "",
                    )
                )
                # Two writes per Faturamento since 2026-08-06: the task first,
                # then the container-26 row that hangs off it. Rendered as two
                # blocks so the reader sees which payload goes where.
                if item.core is not None:
                    self._add("    " + messages.REPORT_FATURAMENTO_TASK_PAYLOAD)
                    self._payload_lines(item.core, indent=6)
                    self._add("    " + messages.REPORT_FATURAMENTO_ROW_PAYLOAD)
                    self._payload_lines(item.result, indent=6)
                else:
                    self._payload_lines(item.result, indent=4)

    def _payload_lines(self, result: MappingResult, indent: int = 4) -> None:
        written = result.by_outcome(Outcome.WRITTEN)
        pad = " " * indent
        if not written:
            self._add(messages.REPORT_NOTHING)
            return
        width = max(len(record.target_column) for record in written)
        for record in written:
            origin = f"   <- {record.source_label}" if record.source_label else ""
            self._add(
                f"{pad}{record.target_column:<{width}} = "
                f"{record.written_value!r}{origin}"
            )
        # The generated task comment is built by the mapper, not by a mapping
        # entry, so it needs its own line.
        comment = result.payload.get("comment")
        if comment and not any(r.target_column == "comment" for r in written):
            self._add(f"{pad}{messages.REPORT_TASK_COMMENT}")
            for line in str(comment).splitlines():
                self._add(f"{pad}  | {line}")

    def _tree_lines(self) -> None:
        """Redmine tree with each node's fate and its GLPI id when known."""
        from transform.tree import Disposition  # local import: avoids a cycle

        templates = {
            Disposition.PROJECT: messages.REPORT_TREE_PROJECT,
            Disposition.TASK: messages.REPORT_TREE_TASK,
            Disposition.FATURAMENTO: messages.REPORT_TREE_FATURAMENTO,
            Disposition.SKIPPED: messages.REPORT_TREE_SKIPPED,
        }
        for planned in self._plan.tree_nodes:
            glpi_id = self._plan.glpi_ids.get(planned.issue_id)
            fields = {
                "issue_id": planned.issue_id,
                "tracker": f"{planned.tracker_id} {planned.tracker_name}".strip(),
                "subject": planned.subject,
                "reason": planned.reason,
                "glpi": f" GLPI {glpi_id}" if glpi_id else "",
            }
            template = templates[planned.disposition]
            self._add(" " * (planned.depth * 2) + template.format(**fields))

    def _section_no_counterpart(self) -> None:
        self._section(messages.REPORT_SECTION_2)
        self._add(messages.REPORT_SECTION_2_INTRO)
        self._records(
            [r for r in self._plan.all_records() if r.outcome is Outcome.NO_COUNTERPART]
        )

    def _section_empty_source(self) -> None:
        self._section(messages.REPORT_SECTION_3)
        self._add(messages.REPORT_SECTION_3_INTRO)
        self._records(
            [r for r in self._plan.all_records() if r.outcome is Outcome.EMPTY_SOURCE],
            show_value=False,
        )

    def _section_unresolved(self) -> None:
        self._section(messages.REPORT_SECTION_4)
        self._add(messages.REPORT_SECTION_4_INTRO)
        self._records(
            [r for r in self._plan.all_records() if r.outcome is Outcome.UNRESOLVED]
        )

    def _section_mandatory(self) -> None:
        """Missing mandatory columns, split by whether they block the write.

        Container 15 refuses the project; container 26 only degrades the row.
        Reporting them under one heading would teach the wrong lesson right
        after the failure that made this distinction matter.
        """
        self._section(messages.REPORT_SECTION_5)
        self._add(messages.REPORT_SECTION_5_INTRO)

        blocking = [
            *self._plan.core.missing_mandatory,
            *self._plan.container15.missing_mandatory,
        ]
        non_blocking = []
        for item in self._plan.faturamento:
            if item.core is not None:
                non_blocking.extend(item.core.missing_mandatory)
            non_blocking.extend(item.result.missing_mandatory)
            non_blocking.extend(self._unmapped_mandatory(item))

        if not blocking and not non_blocking:
            self._add(messages.REPORT_NOTHING)
            return

        for header, records in (
            (messages.REPORT_SECTION_5_BLOCKING, blocking),
            (messages.REPORT_SECTION_5_NON_BLOCKING, non_blocking),
        ):
            if not records:
                continue
            self._add()
            self._add(header)
            for record in records:
                origin = f" [{record.origin}]" if record.origin else ""
                self._add(
                    f"  - {record.target_column}{origin} "
                    f"(origem: {record.source_label}) — {record.detail}"
                )

    @staticmethod
    def _unmapped_mandatory(item: PlannedFaturamento) -> list[FieldRecord]:
        """Mandatory container-26 columns that no mapping entry even claims.

        `missing_mandatory` can only report columns a mapping entry produced a
        record for. A column that no entry claims at all would otherwise vanish
        from section 5 entirely - exactly the silent gap spec 13 rule 7 forbids.

        Columns that ARE mapped are skipped here even when they did not reach
        the payload: an unresolved value already has its own FieldRecord, and
        emitting a second line would double-count it in the same section.
        """
        claimed = {
            record.target_column for record in item.result.records if record.target_column
        }
        return [
            FieldRecord(
                source_label=messages.REPORT_NO_SOURCE,
                outcome=Outcome.EMPTY_SOURCE,
                target_column=column,
                mandatory=True,
                origin=f"RDM {item.issue_id}",
                detail=messages.REPORT_MANDATORY_UNMAPPED,
            )
            for column in MANDATORY_CONTAINER26_COLUMNS
            if column not in item.result.payload and column not in claimed
        ]

    def _section_skipped_children(self) -> None:
        self._section(messages.REPORT_SECTION_SKIPPED)
        self._add(messages.REPORT_SECTION_SKIPPED_INTRO)
        if not self._plan.skipped_children:
            self._add(messages.REPORT_NOTHING)
            return
        for child in self._plan.skipped_children:
            self._add(
                "  "
                + messages.REPORT_SKIPPED_LINE.format(
                    tracker=f"{child.tracker_id} {child.tracker_name}".strip(),
                    issue_id=child.issue_id,
                    subject=child.subject,
                    reason=child.reason,
                )
            )

    def _section_relations(self) -> None:
        self._section(messages.REPORT_SECTION_RELATIONS)
        self._add(messages.REPORT_SECTION_RELATIONS_INTRO)
        if not self._plan.ignored_relations:
            self._add(messages.REPORT_NOTHING)
            return
        for related in self._plan.ignored_relations:
            self._add(
                messages.REPORT_RELATION_LINE.format(
                    issue_id=related.issue_id,
                    tracker=f"{related.tracker_id} {related.tracker_name}".strip(),
                    subject=related.subject,
                    relation_type=related.relation_type,
                )
            )

    def _section_never_write(self) -> None:
        self._section(messages.REPORT_SECTION_6)
        self._records(self._plan.never_write, show_value=False)

    def _section_integrity(self) -> tuple[bool, dict[str, int]]:
        """Prove arithmetically that no source field was dropped."""
        records = self._plan.all_records()
        counts = {
            Outcome.WRITTEN: 0,
            Outcome.EMPTY_SOURCE: 0,
            Outcome.NO_COUNTERPART: 0,
            Outcome.UNRESOLVED: 0,
        }
        for record in records:
            if record.outcome in counts:
                counts[record.outcome] += 1

        total = len(records)
        parts_sum = sum(counts.values())

        self._section(messages.REPORT_SECTION_7)
        self._add(messages.REPORT_INTEGRITY_INTRO)
        self._add(messages.REPORT_INTEGRITY_TOTAL.format(total=total))
        self._add(messages.REPORT_INTEGRITY_WRITTEN.format(count=counts[Outcome.WRITTEN]))
        self._add(messages.REPORT_INTEGRITY_EMPTY.format(count=counts[Outcome.EMPTY_SOURCE]))
        self._add(
            messages.REPORT_INTEGRITY_NO_COUNTERPART.format(
                count=counts[Outcome.NO_COUNTERPART]
            )
        )
        self._add(
            messages.REPORT_INTEGRITY_UNRESOLVED.format(count=counts[Outcome.UNRESOLVED])
        )
        parts = " + ".join(str(value) for value in counts.values())
        ok = parts_sum == total
        self._add(
            messages.REPORT_INTEGRITY_OK.format(parts=parts, total=total)
            if ok
            else messages.REPORT_INTEGRITY_FAIL.format(parts=parts_sum, total=total)
        )
        return ok, counts

    # -- public API --------------------------------------------------------

    def render(self) -> str:
        self._lines = []
        self._header()
        self._section_what()
        self._section_no_counterpart()
        self._section_empty_source()
        self._section_unresolved()
        self._section_mandatory()
        self._section_skipped_children()
        self._section_relations()
        self._section_never_write()
        self._integrity_ok, _ = self._section_integrity()

        for note in self._plan.notes:
            self._add()
            self._add(note)

        self._add()
        self._add(RULE)
        if not self._apply:
            self._add(messages.REPORT_NOTHING_WRITTEN)
            self._add(RULE)
        return "\n".join(self._lines)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.render(), encoding="utf-8")
        return target


def default_report_path(issue_id: int, directory: str | Path = ".") -> Path:
    """report_<issue_id>_<timestamp>.txt, per spec section 10."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(directory) / f"report_{issue_id}_{stamp}.txt"

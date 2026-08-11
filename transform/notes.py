"""Decide where each Redmine note lands in GLPI.

A Redmine issue keeps its narrative in `journals`: one entry per change, each
carrying an optional free-text `notes` plus a `details` list describing what was
changed. The text entries are what the team actually reads - meeting outcomes,
scope changes, "ACOMPANHAMENTO DO PROJETO" - and without them a migrated project
has data and files but no history of decisions.

A journal entry with text becomes a GLPI **Notepad** row (the "Notas" tab) on
the item its issue became. Host assignment is the attachment rule, unchanged:

    PROJECT     (root)             -> the Project
    TASK                           -> its ProjectTask
    FATURAMENTO (child or relation)-> its ProjectTask of type Faturamento
    SKIPPED / unreachable          -> no host, report only

Verified live 2026-08-11 (GLPI 11.0.6): `GET /Project/<id>/Notepad` and
`GET /ProjectTask/<id>/Notepad` both answer with a list, `GET /Notepad` returns
rows with itemtype Project and ProjectTask alike, and `GET /getActiveProfile`
has NO `notepad` key - a Notepad row rides on the host item's right, which the
profile already has (project 1151, projecttask 1151).

This module is pure: it reads issue dicts and writes plan objects. Nothing is
fetched and nothing is written here.

Reporting contract (spec 13, rule 7): every journal entry gets a PlannedNote,
including the ones that will never be migrated. A history-only entry (a status
change with no text) is not a loss, but it must still be visible in the count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# The host rule is IDENTICAL to the attachment one, so it is imported rather
# than copied: a change to the disposition -> host mapping must not be able to
# drift between files and notes.
from transform.attachments import (
    ITEMTYPE_PROJECTTASK,
    host_for,
    item_label,
)

__all__ = [
    "NoteOutcome",
    "PlannedNote",
    "journals_of",
    "plan_notes",
    "summarise",
    "SUCCESS_OUTCOMES",
    "SKIPPED_OUTCOMES",
    "FAILED_OUTCOMES",
]


class NoteOutcome(str, Enum):
    """What happened - or is planned to happen - to one journal entry.

    Deliberately neither transform.mapper.Outcome nor
    transform.attachments.AttachmentOutcome. Section 7 of the report proves
    arithmetically that every source *field* landed in exactly one bucket;
    section 8 does the same for files. Notes get their own enum and their own
    arithmetic in section 9, for the same reason: a note is not a field.
    """

    PLANNED = "planned"                  # will be written on --apply
    WRITTEN = "written"                  # Notepad row created
    DEDUP_GLPI = "dedup_glpi"            # marker already present on the host
    DEDUP_LOCAL = "dedup_local"          # already in migration_map
    NO_HOST = "no_host"                  # issue not migrated -> report only
    HISTORY_ONLY = "history_only"        # journal with no text -> nothing to write
    SKIPPED_BY_FLAG = "skipped_by_flag"  # --skip-notes
    FAILED_WRITE = "failed_write"


# Outcomes that mean "nothing left to do, and that is fine".
SUCCESS_OUTCOMES = frozenset(
    {NoteOutcome.WRITTEN, NoteOutcome.DEDUP_GLPI, NoteOutcome.DEDUP_LOCAL}
)

# Outcomes that mean "deliberately not migrated".
SKIPPED_OUTCOMES = frozenset(
    {
        NoteOutcome.NO_HOST,
        NoteOutcome.HISTORY_ONLY,
        NoteOutcome.SKIPPED_BY_FLAG,
    }
)

FAILED_OUTCOMES = frozenset({NoteOutcome.FAILED_WRITE})

# PT-BR details, rendered straight into the report.
DETAIL_OUT_OF_SCOPE = "issue não migrada para o GLPI"
DETAIL_UNREACHABLE = "issue não pôde ser lida no Redmine"
DETAIL_HISTORY_ONLY = "entrada de histórico sem texto"
DETAIL_SKIPPED_BY_FLAG = "ignorada por --skip-notes"


@dataclass
class PlannedNote:
    """One Redmine journal entry and the GLPI Notepad row it becomes."""

    journal_id: int
    issue_id: int
    author: str = ""
    created_on: str = ""
    private: bool = False
    text: str = ""
    # Filenames that arrived WITH this note, read from the journal's own
    # details (property "attachment"). The bytes are migrated by step 5 as
    # Documents; the note only names them, so the reader sees which file the
    # text is talking about.
    attachment_names: list[str] = field(default_factory=list)
    # Where it goes. None means "nowhere" - the issue is not in GLPI.
    host_itemtype: str | None = None
    host_redmine_id: int | None = None
    # Purely for the report, so a line reads "Tarefa RDM 19769 «...»".
    host_label: str = ""
    outcome: NoteOutcome = NoteOutcome.PLANNED
    detail: str = ""
    glpi_notepad_id: int | None = None

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


def journals_of(issue: dict) -> list[dict]:
    """The journal dicts of one issue.

    Same verified trap as `children` and `attachments`: the key may be absent
    entirely, and Redmine sends `null` where other issues send `[]`.
    """
    return [item for item in (issue.get("journals") or []) if isinstance(item, dict)]


def attachment_names_of(journal: dict) -> list[str]:
    """Filenames attached in this journal entry.

    Redmine records the upload as a detail row: property "attachment", `name`
    holding the ATTACHMENT id (as a string) and `new_value` the filename. That
    is the only place the note -> file association exists; the issue's own
    `attachments` list is flat and says nothing about which note brought what.
    """
    names: list[str] = []
    for detail in journal.get("details") or []:
        if not isinstance(detail, dict) or detail.get("property") != "attachment":
            continue
        name = str(detail.get("new_value") or "").strip()
        if name:
            names.append(name)
    return names


def plan_notes(
    tree_plan,
    faturamento_issues: list[dict],
    root_issue_id: int,
    tree_failures: list | None = None,
    skip: bool = False,
) -> list[PlannedNote]:
    """Every journal entry of the tree, with its host resolved.

    `faturamento_issues` covers the relation path only - a tracker-15 descendant
    is already a node of `tree_plan` and would otherwise be counted twice.
    """
    planned: list[PlannedNote] = []
    seen_issues: set[int] = set()

    for node in tree_plan.nodes:
        seen_issues.add(node.issue_id)
        host_itemtype, host_id, host_label, detail = host_for(node, root_issue_id)
        planned.extend(
            _for_issue(
                node.node.issue,
                host_itemtype=host_itemtype,
                host_redmine_id=host_id,
                host_label=host_label,
                detail=detail,
                skip=skip,
            )
        )

    for issue in faturamento_issues:
        issue_id = int(issue["id"])
        if issue_id in seen_issues:
            continue
        seen_issues.add(issue_id)
        planned.extend(
            _for_issue(
                issue,
                host_itemtype=ITEMTYPE_PROJECTTASK,
                host_redmine_id=issue_id,
                host_label=item_label("Faturamento", issue_id, issue.get("subject") or ""),
                detail="",
                skip=skip,
            )
        )

    # A child we could not read may well have carried notes. We cannot list them
    # - the GET failed - but the report must not imply the issue had none.
    for failure in tree_failures or []:
        issue_id = failure[0] if isinstance(failure, (tuple, list)) else failure
        if int(issue_id) in seen_issues:
            continue
        planned.append(
            PlannedNote(
                journal_id=0,
                issue_id=int(issue_id),
                host_label=item_label("RDM", int(issue_id), ""),
                outcome=NoteOutcome.NO_HOST,
                detail=DETAIL_UNREACHABLE,
            )
        )

    return planned


def _for_issue(
    issue: dict,
    host_itemtype: str | None,
    host_redmine_id: int | None,
    host_label: str,
    detail: str,
    skip: bool,
) -> list[PlannedNote]:
    issue_id = int(issue["id"])
    result: list[PlannedNote] = []

    for raw in journals_of(issue):
        item = PlannedNote(
            journal_id=int(raw.get("id") or 0),
            issue_id=issue_id,
            author=str((raw.get("user") or {}).get("name") or ""),
            created_on=str(raw.get("created_on") or ""),
            private=bool(raw.get("private_notes")),
            text=str(raw.get("notes") or ""),
            attachment_names=attachment_names_of(raw),
            host_itemtype=host_itemtype,
            host_redmine_id=host_redmine_id,
            host_label=host_label,
        )

        # Order matters. A history-only entry is classified as such even when
        # the issue is out of scope: there is nothing to migrate either way, and
        # calling it NO_HOST would inflate the "lost because out of scope"
        # figure with entries that were never going to be written.
        if not item.has_text:
            item.outcome = NoteOutcome.HISTORY_ONLY
            item.detail = DETAIL_HISTORY_ONLY
        elif host_itemtype is None:
            item.outcome = NoteOutcome.NO_HOST
            item.detail = detail or DETAIL_OUT_OF_SCOPE
        elif skip:
            item.outcome = NoteOutcome.SKIPPED_BY_FLAG
            item.detail = DETAIL_SKIPPED_BY_FLAG

        result.append(item)

    return result


def summarise(planned: list[PlannedNote]) -> dict[str, int]:
    """Counters for report section 9 and the web panel cards."""
    pending = sum(1 for item in planned if item.outcome is NoteOutcome.PLANNED)
    done = sum(1 for item in planned if item.outcome in SUCCESS_OUTCOMES)
    skipped = sum(1 for item in planned if item.outcome in SKIPPED_OUTCOMES)
    failed = sum(1 for item in planned if item.outcome in FAILED_OUTCOMES)
    return {
        "total": len(planned),
        "pending": pending,
        "done": done,
        "skipped": skipped,
        "failed": failed,
        # Split out of `skipped` for the report: a history-only entry is not a
        # loss, while a note on an out-of-scope issue is text that stays behind.
        "history_only": sum(
            1 for item in planned if item.outcome is NoteOutcome.HISTORY_ONLY
        ),
        "with_text": sum(1 for item in planned if item.journal_id and item.has_text),
    }

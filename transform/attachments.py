"""Decide where each Redmine attachment lands in GLPI.

A Redmine attachment becomes a GLPI **Document** linked to the item its issue
became (`Document_Item`). GLPI 11.0.6 lists both `Project` and `ProjectTask` in
`CFG_GLPI['document_types']`, so both hosts carry a "Documentos" tab.

Host assignment follows the tree disposition and nothing else:

    PROJECT     (root)             -> the Project
    TASK                           -> its ProjectTask
    FATURAMENTO (child or relation)-> its ProjectTask of type Faturamento
    SKIPPED / unreachable          -> no host, report only

Container 26 has no file column, so a Faturamento's files hang off the task
itself, exactly like a Compras task's.

This module is pure: it reads issue dicts and writes plan objects. Nothing is
downloaded and nothing is uploaded here - the dry-run gets its filenames and
sizes from the Redmine metadata alone, which is what keeps `--apply`-free runs
free of network traffic beyond the reads the plan already does.

Reporting contract (spec 13, rule 7): every attachment gets a PlannedAttachment,
including the ones that will never be migrated. A file that cannot reach GLPI
must still be visible in the report, with its issue, name and size.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from config.settings import DOCUMENT_MAX_SIZE_BYTES
from transform.tree import Disposition

ITEMTYPE_PROJECT = "Project"
ITEMTYPE_PROJECTTASK = "ProjectTask"


class AttachmentOutcome(str, Enum):
    """What happened - or is planned to happen - to one attachment.

    Deliberately NOT transform.mapper.Outcome. That enum classifies source
    *fields* and report section 7 proves arithmetically that every field landed
    in exactly one of its buckets; mixing files into it would break that proof.
    Attachments get their own enum and their own arithmetic in section 8.
    """

    PLANNED = "planned"                  # will be uploaded on --apply
    UPLOADED = "uploaded"                # written (filled in during apply)
    DEDUP_GLPI = "dedup_glpi"            # marker already present in GLPI
    DEDUP_LOCAL = "dedup_local"          # already in migration_map
    NO_HOST = "no_host"                  # issue not migrated -> report only
    TOO_BIG = "too_big"                  # over GLPI's document_max_size
    SKIPPED_BY_FLAG = "skipped_by_flag"  # --skip-attachments
    FAILED_DOWNLOAD = "failed_download"
    FAILED_UPLOAD = "failed_upload"
    FAILED_LINK = "failed_link"


# Outcomes that mean "nothing left to do, and that is fine".
SUCCESS_OUTCOMES = frozenset(
    {AttachmentOutcome.UPLOADED, AttachmentOutcome.DEDUP_GLPI, AttachmentOutcome.DEDUP_LOCAL}
)

# Outcomes that mean "deliberately not migrated".
SKIPPED_OUTCOMES = frozenset(
    {
        AttachmentOutcome.NO_HOST,
        AttachmentOutcome.TOO_BIG,
        AttachmentOutcome.SKIPPED_BY_FLAG,
    }
)

FAILED_OUTCOMES = frozenset(
    {
        AttachmentOutcome.FAILED_DOWNLOAD,
        AttachmentOutcome.FAILED_UPLOAD,
        AttachmentOutcome.FAILED_LINK,
    }
)

# PT-BR details, rendered straight into the report.
DETAIL_OUT_OF_SCOPE = "issue não migrada para o GLPI"
DETAIL_UNREACHABLE = "issue não pôde ser lida no Redmine"
DETAIL_TOO_BIG = "excede o limite do GLPI ({limit_mb} MB)"
DETAIL_SKIPPED_BY_FLAG = "ignorado por --skip-attachments"
DETAIL_EXT_UNKNOWN = (
    "extensão '{ext}' não consta em glpi_documenttypes — o GLPI pode recusar"
)


@dataclass
class PlannedAttachment:
    """One Redmine attachment and the GLPI document it becomes."""

    attachment_id: int
    issue_id: int
    filename: str
    filesize: int
    content_type: str = ""
    description: str = ""
    content_url: str = ""
    author: str = ""
    created_on: str = ""
    # Where it goes. None means "nowhere" - the issue is not in GLPI.
    host_itemtype: str | None = None
    host_redmine_id: int | None = None
    # Purely for the report, so a line reads "Tarefa RDM 19769 «...»".
    host_label: str = ""
    outcome: AttachmentOutcome = AttachmentOutcome.PLANNED
    detail: str = ""
    # Warning carried alongside the outcome: the extension is unknown to GLPI.
    # Never an outcome of its own - we still attempt the upload, see below.
    warning: str = ""
    glpi_document_id: int | None = None

    @property
    def extension(self) -> str:
        _, _, ext = self.filename.rpartition(".")
        return ext.lower() if ext and ext != self.filename else ""


def attachments_of(issue: dict) -> list[dict]:
    """The attachment dicts of one issue.

    Same verified trap as `children`: the key may be absent entirely, and
    Redmine sends `null` where other issues send `[]`.
    """
    return [item for item in (issue.get("attachments") or []) if isinstance(item, dict)]


def plan_attachments(
    tree_plan,
    faturamento_issues: list[dict],
    root_issue_id: int,
    tree_failures: list | None = None,
    document_types: set[str] | None = None,
    skip: bool = False,
) -> list[PlannedAttachment]:
    """Every attachment of the tree, with its host resolved.

    `faturamento_issues` covers the relation path only - a tracker-15 descendant
    is already a node of `tree_plan` and would otherwise be counted twice.
    """
    planned: list[PlannedAttachment] = []
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
                document_types=document_types,
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
                document_types=document_types,
                skip=skip,
            )
        )

    # A child we could not read may well have had files. We cannot list them -
    # the GET failed - but the report must not imply the issue had none.
    for failure in tree_failures or []:
        issue_id = failure[0] if isinstance(failure, (tuple, list)) else failure
        if int(issue_id) in seen_issues:
            continue
        planned.append(
            PlannedAttachment(
                attachment_id=0,
                issue_id=int(issue_id),
                filename="",
                filesize=0,
                host_label=item_label("RDM", int(issue_id), ""),
                outcome=AttachmentOutcome.NO_HOST,
                detail=DETAIL_UNREACHABLE,
            )
        )

    return planned


def host_for(node, root_issue_id: int) -> tuple[str | None, int | None, str, str]:
    """Where a planned node's payload lands: (itemtype, redmine id, label, why not).

    Public because transform.notes imports it. The disposition -> host rule is
    the same for a file and for a note, and it must not be able to drift
    between the two modules.
    """
    if node.disposition is Disposition.PROJECT:
        return (
            ITEMTYPE_PROJECT,
            root_issue_id,
            item_label("Projeto", node.issue_id, node.subject),
            "",
        )
    if node.disposition is Disposition.TASK:
        return (
            ITEMTYPE_PROJECTTASK,
            node.issue_id,
            item_label("Tarefa", node.issue_id, node.subject),
            "",
        )
    if node.disposition is Disposition.FATURAMENTO:
        return (
            ITEMTYPE_PROJECTTASK,
            node.issue_id,
            item_label("Faturamento", node.issue_id, node.subject),
            "",
        )
    return (
        None,
        None,
        item_label("RDM", node.issue_id, node.subject),
        node.reason or DETAIL_OUT_OF_SCOPE,
    )


def item_label(kind: str, issue_id: int, subject: str) -> str:
    text = f"{kind} RDM {issue_id}"
    if subject:
        text += f" «{subject}»"
    return text


def _for_issue(
    issue: dict,
    host_itemtype: str | None,
    host_redmine_id: int | None,
    host_label: str,
    detail: str,
    document_types: set[str] | None,
    skip: bool,
) -> list[PlannedAttachment]:
    issue_id = int(issue["id"])
    result: list[PlannedAttachment] = []

    for raw in attachments_of(issue):
        item = PlannedAttachment(
            attachment_id=int(raw.get("id") or 0),
            issue_id=issue_id,
            filename=str(raw.get("filename") or "").strip(),
            filesize=int(raw.get("filesize") or 0),
            content_type=str(raw.get("content_type") or ""),
            description=str(raw.get("description") or "").strip(),
            content_url=str(raw.get("content_url") or ""),
            author=str((raw.get("author") or {}).get("name") or ""),
            created_on=str(raw.get("created_on") or ""),
            host_itemtype=host_itemtype,
            host_redmine_id=host_redmine_id,
            host_label=host_label,
        )

        if host_itemtype is None:
            item.outcome = AttachmentOutcome.NO_HOST
            item.detail = detail or DETAIL_OUT_OF_SCOPE
        elif skip:
            item.outcome = AttachmentOutcome.SKIPPED_BY_FLAG
            item.detail = DETAIL_SKIPPED_BY_FLAG
        elif item.filesize > DOCUMENT_MAX_SIZE_BYTES:
            item.outcome = AttachmentOutcome.TOO_BIG
            item.detail = DETAIL_TOO_BIG.format(
                limit_mb=DOCUMENT_MAX_SIZE_BYTES // (1024 * 1024)
            )

        # A warning, never an outcome. GLPI stores `ext` as a pattern in some
        # rows, so a plain string miss can be a false alarm - GLPI itself stays
        # the authority and a real refusal is reported as FAILED_UPLOAD.
        if document_types is not None and item.extension:
            if item.extension not in document_types:
                item.warning = DETAIL_EXT_UNKNOWN.format(ext=item.extension)

        result.append(item)

    return result


def summarise(planned: list[PlannedAttachment]) -> dict[str, int]:
    """Counters for report section 8 and the web panel cards."""
    pending = sum(1 for item in planned if item.outcome is AttachmentOutcome.PLANNED)
    done = sum(1 for item in planned if item.outcome in SUCCESS_OUTCOMES)
    skipped = sum(1 for item in planned if item.outcome in SKIPPED_OUTCOMES)
    failed = sum(1 for item in planned if item.outcome in FAILED_OUTCOMES)
    return {
        "total": len(planned),
        "pending": pending,
        "done": done,
        "skipped": skipped,
        "failed": failed,
        "bytes": sum(
            item.filesize
            for item in planned
            if item.outcome in SUCCESS_OUTCOMES or item.outcome is AttachmentOutcome.PLANNED
        ),
    }

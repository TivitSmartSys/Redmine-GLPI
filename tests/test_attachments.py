"""Attachments: host assignment, the report contract, and apply degradation.

Three things are pinned here, in order of how expensive they would be to
rediscover:

  1. an attachment lands on the item its issue became, and an attachment whose
     issue is out of scope is reported rather than dropped;
  2. attachments do NOT change the section-7 arithmetic. That number is the one
     guarantee the whole report exists to make, and files are not fields;
  3. a failing upload degrades - the rest of the files still go, and the note
     reaches the report. The project is already written by then; aborting would
     leave it half migrated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from clients.errors import GlpiError  # noqa: E402
from clients.glpi import GlpiClient, _has_exact_marker  # noqa: E402
from config.settings import (  # noqa: E402
    DOCUMENT_MAX_SIZE_BYTES,
    ITEMTYPE_DOCUMENT,
    SEARCH_FETCH_RANGE,
)
from report.reporter import ProjectPlan, Reporter  # noqa: E402
from transform.attachments import (  # noqa: E402
    AttachmentOutcome,
    PlannedAttachment,
    plan_attachments,
)
from transform.mapper import FieldRecord, MappingResult, Outcome  # noqa: E402
from transform.tree import plan_tree  # noqa: E402
from clients.redmine import TreeNode  # noqa: E402


# -- fixtures ---------------------------------------------------------------


def attachment(attachment_id: int, filename: str = "arquivo.pdf", size: int = 1024) -> dict:
    return {
        "id": attachment_id,
        "filename": filename,
        "filesize": size,
        "content_type": "application/pdf",
        "description": "Espelho de nota",
        "content_url": f"http://redmine/attachments/download/{attachment_id}/{filename}",
        "author": {"name": "Fulano"},
        "created_on": "2024-03-11T10:00:00Z",
    }


def issue(issue_id: int, tracker_id: int, attachments: list | None = None, **extra) -> dict:
    payload = {
        "id": issue_id,
        "subject": f"Issue {issue_id}",
        "tracker": {"id": tracker_id, "name": f"Tracker {tracker_id}"},
        "custom_fields": [],
    }
    if attachments is not None:
        payload["attachments"] = attachments
    payload.update(extra)
    return payload


def tree_with_children(children: list[TreeNode], root_attachments: list) -> TreeNode:
    return TreeNode(
        issue=issue(16467, 14, root_attachments),
        children=children,
    )


# -- host assignment --------------------------------------------------------


def test_host_follows_the_disposition_of_the_issue():
    """Project / task / faturamento / out-of-scope each land where they should."""
    root = tree_with_children(
        children=[
            TreeNode(issue=issue(19769, 41, [attachment(2)])),   # Compras -> task
            TreeNode(issue=issue(20000, 15, [attachment(3)])),   # Faturamento
            TreeNode(issue=issue(30000, 39, [attachment(4)])),   # CEMIG -> skipped
        ],
        root_attachments=[attachment(1)],
    )

    planned = plan_attachments(plan_tree(root), [], root_issue_id=16467)
    hosts = {item.attachment_id: (item.host_itemtype, item.host_redmine_id) for item in planned}

    assert hosts[1] == ("Project", 16467)
    assert hosts[2] == ("ProjectTask", 19769)
    assert hosts[3] == ("ProjectTask", 20000)
    assert hosts[4] == (None, None)

    out_of_scope = next(item for item in planned if item.attachment_id == 4)
    assert out_of_scope.outcome is AttachmentOutcome.NO_HOST
    assert out_of_scope.detail  # never silent: the report needs a reason


def journal(journal_id: int, attachment_ids: list[int], text: str = "") -> dict:
    """A journal entry that carried some files, in Redmine's own shape."""
    return {
        "id": journal_id,
        "user": {"name": "jeane.silva"},
        "created_on": "2026-03-31T19:06:04Z",
        "notes": text,
        "details": [
            {"property": "attachment", "name": str(a), "new_value": f"arquivo{a}.pdf"}
            for a in attachment_ids
        ],
    }


def test_every_file_is_hosted_by_its_item_and_notes_only_add_a_link():
    """The Documentos tab is where users look, so the ITEM host is universal.

    A file that came with a note gets `host_journal_id` on top of that, and
    step 6 turns it into a second Document_Item. It never replaces the first.
    """
    root = TreeNode(
        issue=issue(
            16467, 14,
            [attachment(1), attachment(2), attachment(3)],
            journals=[
                journal(155253, [2], text="Segue a TAP."),  # a real note
                journal(155302, [3]),                       # upload, no comment
            ],
        )
    )
    planned = plan_attachments(plan_tree(root), [], root_issue_id=16467)
    by_id = {item.attachment_id: item for item in planned}

    # All three live on the project - that is the Documentos tab.
    assert all(by_id[n].host_itemtype == "Project" for n in (1, 2, 3))
    assert all(by_id[n].host_redmine_id == 16467 for n in (1, 2, 3))

    # Only the one that came with a TEXT note also points at a journal.
    assert by_id[2].host_journal_id == 155253
    # Attachment 1 belongs to no journal, attachment 3 to a text-less one:
    # neither has a note to be linked to.
    assert by_id[1].host_journal_id is None
    assert by_id[3].host_journal_id is None


def test_note_file_on_an_out_of_scope_issue_has_no_host_at_all():
    """No item and no note there, so there is nothing to link to."""
    root = tree_with_children(
        children=[
            TreeNode(issue=issue(30000, 39, [attachment(4)],
                                 journals=[journal(999, [4], text="nota")]))
        ],
        root_attachments=[],
    )
    planned = plan_attachments(plan_tree(root), [], root_issue_id=16467)
    item = next(p for p in planned if p.attachment_id == 4)
    assert item.outcome is AttachmentOutcome.NO_HOST
    assert item.host_journal_id is None


def test_relation_faturamento_is_counted_once():
    """A tracker-15 partner arrives through relations, a descendant through the
    tree. Both paths feeding the same issue must not double the files."""
    partner = issue(17539, 15, [attachment(9)])
    root = tree_with_children(children=[], root_attachments=[])

    planned = plan_attachments(plan_tree(root), [partner], root_issue_id=16467)
    assert [item.attachment_id for item in planned] == [9]
    assert planned[0].host_itemtype == "ProjectTask"
    assert planned[0].host_redmine_id == 17539


def test_missing_attachments_key_is_not_a_crash():
    """Verified trap: Redmine omits the key entirely on some issues."""
    root = TreeNode(issue=issue(16467, 14))  # no 'attachments' key at all
    assert plan_attachments(plan_tree(root), [], root_issue_id=16467) == []


def test_unreadable_child_is_still_visible():
    """A child whose GET failed may have had files; the report must not imply
    it had none."""
    root = tree_with_children(children=[], root_attachments=[])
    planned = plan_attachments(
        plan_tree(root), [], root_issue_id=16467, tree_failures=[(18888, "erro")]
    )
    assert len(planned) == 1
    assert planned[0].issue_id == 18888
    assert planned[0].outcome is AttachmentOutcome.NO_HOST


def test_oversized_file_is_skipped_before_any_download():
    root = tree_with_children(
        children=[], root_attachments=[attachment(1, size=DOCUMENT_MAX_SIZE_BYTES + 1)]
    )
    planned = plan_attachments(plan_tree(root), [], root_issue_id=16467)
    assert planned[0].outcome is AttachmentOutcome.TOO_BIG


def test_skip_flag_keeps_the_files_in_the_report():
    """--skip-attachments must not make files disappear, only unmigrated."""
    root = tree_with_children(children=[], root_attachments=[attachment(1)])
    planned = plan_attachments(plan_tree(root), [], root_issue_id=16467, skip=True)
    assert planned[0].outcome is AttachmentOutcome.SKIPPED_BY_FLAG


def test_unknown_extension_warns_without_blocking():
    """GLPI stays the authority: an unlisted extension is a warning, not a skip."""
    root = tree_with_children(
        children=[], root_attachments=[attachment(1, filename="planilha.xyz")]
    )
    planned = plan_attachments(
        plan_tree(root), [], root_issue_id=16467, document_types={"pdf"}
    )
    assert planned[0].outcome is AttachmentOutcome.PLANNED
    assert "xyz" in planned[0].warning


# -- the reporting contract -------------------------------------------------


def minimal_plan(attachments: list) -> ProjectPlan:
    return ProjectPlan(
        issue=issue(16467, 14),
        core=MappingResult(
            payload={"name": "Projeto"},
            records=[FieldRecord(source_label="Assunto", outcome=Outcome.WRITTEN,
                                 target_column="name")],
        ),
        container15=MappingResult(
            payload={"rdmfield": "16467"},
            records=[FieldRecord(source_label="rdmfield", outcome=Outcome.WRITTEN,
                                 target_column="rdmfield")],
        ),
        attachments=attachments,
    )


def test_attachments_do_not_touch_the_field_arithmetic():
    """Section 7 counts source FIELDS. Files must not appear in that total."""
    files = [
        PlannedAttachment(
            attachment_id=1, issue_id=16467, filename="a.pdf", filesize=10,
            host_itemtype="Project", host_redmine_id=16467, host_label="Projeto RDM 16467",
        )
    ]
    without = Reporter(minimal_plan([])).render()
    with_files = Reporter(minimal_plan(files)).render()

    def integrity_block(text: str) -> str:
        start = text.index("7. CONFER")
        return text[start : text.index("8. ANEXOS")]

    assert integrity_block(without) == integrity_block(with_files)
    assert len(minimal_plan(files).all_records()) == len(minimal_plan([]).all_records())


def test_section_8_closes_its_own_arithmetic():
    files = [
        PlannedAttachment(attachment_id=1, issue_id=16467, filename="a.pdf", filesize=10,
                          host_itemtype="Project", host_redmine_id=16467,
                          host_label="Projeto RDM 16467"),
        PlannedAttachment(attachment_id=2, issue_id=30000, filename="b.pdf", filesize=20,
                          outcome=AttachmentOutcome.NO_HOST, host_label="RDM 30000",
                          detail="fora do escopo"),
        PlannedAttachment(attachment_id=3, issue_id=16467, filename="c.pdf", filesize=30,
                          host_itemtype="Project", host_redmine_id=16467,
                          host_label="Projeto RDM 16467",
                          outcome=AttachmentOutcome.FAILED_UPLOAD),
    ]
    text = Reporter(minimal_plan(files)).render()
    assert "1 + 0 + 1 + 1 = 3" in text
    assert "nenhum anexo foi descartado" in text
    # The unmigrated file keeps its own block and its reason.
    assert "NÃO MIGRADOS" in text
    assert "fora do escopo" in text


# -- deduplication marker ---------------------------------------------------


def test_document_links_asks_for_a_full_range():
    """GLPI returns 15 rows without an explicit range.

    Measured on project 1277, which had 19 linked documents and read back as 15.
    A truncated read makes _ensure_link post duplicates GLPI then refuses, so
    the range is not cosmetic.
    """
    seen = {}

    class Recorder(GlpiClient):
        def __init__(self):  # no session, no network
            pass

        def _request(self, _method, path, params=None, **_kwargs):
            seen[path] = params
            return []

    Recorder().document_links("Project", 1277)
    assert seen["/Document_Item"]["range"] == SEARCH_FETCH_RANGE


def test_marker_match_is_line_anchored():
    """searchText is a LIKE '%…%' match; 2931 must never match 29314."""
    comment = "rdmattachment:29314\nMigrado do Redmine — issue 16467"
    assert _has_exact_marker(comment, "rdmattachment:29314")
    assert not _has_exact_marker(comment, "rdmattachment:2931")
    assert not _has_exact_marker(None, "rdmattachment:1")


# -- apply degradation ------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.records = []

    def lookup(self, *_args, **_kwargs):
        return None

    def record(self, redmine_id, glpi_id, itemtype, **kwargs):
        self.records.append((redmine_id, glpi_id, itemtype))


class FakeRedmine:
    def __init__(self, failing: set[int] | None = None):
        self.failing = failing or set()
        self.downloaded = []

    def download_attachment(self, content_url, dest_path):
        Path(dest_path).write_bytes(b"conteudo")
        self.downloaded.append(content_url)
        return 8


class FakeGlpi:
    """Just enough of GlpiClient for apply_attachments."""

    def __init__(self, failing_uploads: set[str] | None = None):
        self.failing_uploads = failing_uploads or set()
        self.uploaded = []
        self.upload_entities = []
        self.links = []
        self._next_id = 500

    def find_document_by_marker(self, _attachment_id):
        return []

    def get_item(self, _itemtype, _item_id):
        return None

    def document_links(self, _itemtype, _items_id):
        return []

    def upload_document(self, path, name, comment, entities_id=None):
        if name in self.failing_uploads:
            raise GlpiError("ERROR_GLPI_ADD")
        self._next_id += 1
        self.uploaded.append((name, comment))
        # A document born in the wrong entity cannot be linked to its item.
        self.upload_entities.append(entities_id)
        return self._next_id

    def link_document(self, document_id, itemtype, items_id):
        self.links.append((document_id, itemtype, items_id))
        return len(self.links)


def test_one_failed_upload_does_not_stop_the_rest():
    files = [
        PlannedAttachment(attachment_id=1, issue_id=16467, filename="ruim.pdf", filesize=10,
                          content_url="http://x/1", host_itemtype="Project",
                          host_redmine_id=16467, host_label="Projeto RDM 16467"),
        PlannedAttachment(attachment_id=2, issue_id=16467, filename="bom.pdf", filesize=10,
                          content_url="http://x/2", host_itemtype="Project",
                          host_redmine_id=16467, host_label="Projeto RDM 16467"),
    ]
    plan = minimal_plan(files)
    plan.glpi_ids = {16467: 1265}
    glpi = FakeGlpi(failing_uploads={"ruim.pdf"})
    store = FakeStore()

    main.apply_attachments(glpi, FakeRedmine(), plan, store)

    assert files[0].outcome is AttachmentOutcome.FAILED_UPLOAD
    assert files[1].outcome is AttachmentOutcome.UPLOADED
    assert files[1].glpi_document_id
    assert plan.notes  # the failure reached the report
    assert store.records == [(2, files[1].glpi_document_id, ITEMTYPE_DOCUMENT)]


def test_missing_host_is_reported_not_uploaded():
    """A task whose POST failed leaves its files without a destination."""
    files = [
        PlannedAttachment(attachment_id=1, issue_id=19769, filename="a.pdf", filesize=10,
                          content_url="http://x/1", host_itemtype="ProjectTask",
                          host_redmine_id=19769, host_label="Tarefa RDM 19769"),
    ]
    plan = minimal_plan(files)
    plan.glpi_ids = {}  # the task was never created
    glpi = FakeGlpi()

    main.apply_attachments(glpi, FakeRedmine(), plan, FakeStore())

    assert files[0].outcome is AttachmentOutcome.NO_HOST
    assert glpi.uploaded == []
    assert plan.notes


def test_document_is_uploaded_into_the_projects_entity():
    """Regression, RDM 20438 -> project 1285 (2026-08-12).

    The project moved to the entity of its Cliente while preflight left the
    session in the root entity, so every document was created in entity 0 and
    every POST /Document_Item came back "Você não tem permissão" - four files
    uploaded, none linked, an empty Documentos tab. The document must be born
    in the entity of the item it will hang off.
    """
    files = [
        PlannedAttachment(attachment_id=1, issue_id=16467, filename="a.pdf", filesize=10,
                          content_url="http://x/1", host_itemtype="Project",
                          host_redmine_id=16467, host_label="Projeto RDM 16467"),
    ]
    plan = minimal_plan(files)
    plan.core.payload["entities_id"] = 70
    plan.glpi_ids = {16467: 1265}
    glpi = FakeGlpi()

    main.apply_attachments(glpi, FakeRedmine(), plan, FakeStore())

    assert glpi.upload_entities == [70]
    assert files[0].outcome is AttachmentOutcome.UPLOADED


def test_upload_entity_is_none_when_the_plan_has_no_entity():
    """A Mapper built without an entity resolver leaves no entities_id. The
    upload then falls back to the session's entity, as it always did."""
    files = [
        PlannedAttachment(attachment_id=1, issue_id=16467, filename="a.pdf", filesize=10,
                          content_url="http://x/1", host_itemtype="Project",
                          host_redmine_id=16467, host_label="Projeto RDM 16467"),
    ]
    plan = minimal_plan(files)
    plan.glpi_ids = {16467: 1265}
    glpi = FakeGlpi()

    main.apply_attachments(glpi, FakeRedmine(), plan, FakeStore())

    assert glpi.upload_entities == [None]


def test_document_comment_puts_the_marker_on_its_own_first_line():
    item = PlannedAttachment(
        attachment_id=29314, issue_id=16467, filename="a.xlsb", filesize=10,
        description="Memória de cálculo", author="Fulano", created_on="2024-03-11",
    )
    comment = main._document_comment(item)
    assert comment.splitlines()[0] == "rdmattachment:29314"
    assert _has_exact_marker(comment, "rdmattachment:29314")
    assert "Memória de cálculo" in comment

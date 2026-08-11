"""Notes: host assignment, the report contract, the marker, and apply degradation.

The same four things test_attachments.py pins, for the phase that migrates
Redmine journals into GLPI Notepad rows:

  1. a note lands on the item its issue became, and a note whose issue is out of
     scope is reported rather than dropped;
  2. notes change neither the section-7 nor the section-8 arithmetic. Each of
     the three sections proves its own total, and a note is neither a field nor
     a file;
  3. the dedup marker is line-anchored, so rdmnote:1559 cannot match
     rdmnote:155016 - the trap that already bit the document marker;
  4. a failing write degrades. The project, its tasks and its files are already
     in GLPI by then; aborting for one note would leave it half migrated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from clients.errors import GlpiError  # noqa: E402
from clients.glpi import GlpiClient, _has_exact_marker  # noqa: E402
from clients.redmine import TreeNode  # noqa: E402
from config.settings import (  # noqa: E402
    ITEMTYPE_NOTEPAD,
    NOTE_MARKER_PREFIX,
    SEARCH_FETCH_RANGE,
)
from report import messages  # noqa: E402
from report.reporter import ProjectPlan, Reporter  # noqa: E402
from transform.mapper import FieldRecord, MappingResult, Outcome  # noqa: E402
from transform.notes import (  # noqa: E402
    NoteOutcome,
    PlannedNote,
    journals_of,
    plan_notes,
    summarise,
)
from transform.tree import plan_tree  # noqa: E402


# -- fixtures ---------------------------------------------------------------


def journal(
    journal_id: int,
    text: str = "Reunião 12-01: pedido será enviado.",
    private: bool = False,
    attachments: list[tuple[int, str]] | None = None,
) -> dict:
    """One journal entry in the shape Redmine really returns.

    `details` mixes an attribute change with the attachment rows on purpose:
    the real payload does, and attachment_names_of has to ignore the former.
    """
    details = [{"property": "attr", "name": "status_id", "old_value": "1",
                "new_value": "3"}]
    for attachment_id, filename in attachments or []:
        details.append(
            {
                "property": "attachment",
                "name": str(attachment_id),
                "old_value": None,
                "new_value": filename,
            }
        )
    return {
        "id": journal_id,
        "user": {"id": 7, "name": "henrique.sokal"},
        "created_on": "2026-01-12T17:48:59Z",
        "private_notes": private,
        "notes": text,
        "details": details,
    }


def issue(issue_id: int, tracker_id: int, journals: list | None = None, **extra) -> dict:
    payload = {
        "id": issue_id,
        "subject": f"Issue {issue_id}",
        "tracker": {"id": tracker_id, "name": f"Tracker {tracker_id}"},
        "custom_fields": [],
    }
    if journals is not None:
        payload["journals"] = journals
    payload.update(extra)
    return payload


def tree_with_children(children: list[TreeNode], root_journals: list) -> TreeNode:
    return TreeNode(issue=issue(16467, 14, root_journals), children=children)


# -- host assignment --------------------------------------------------------


def test_host_follows_the_disposition_of_the_issue():
    """Project / task / faturamento / out-of-scope each land where they should."""
    root = tree_with_children(
        children=[
            TreeNode(issue=issue(19769, 41, [journal(2)])),   # Compras -> task
            TreeNode(issue=issue(20000, 15, [journal(3)])),   # Faturamento
            TreeNode(issue=issue(30000, 39, [journal(4)])),   # CEMIG -> skipped
        ],
        root_journals=[journal(1)],
    )

    planned = plan_notes(plan_tree(root), [], root_issue_id=16467)
    hosts = {item.journal_id: (item.host_itemtype, item.host_redmine_id) for item in planned}

    assert hosts[1] == ("Project", 16467)
    assert hosts[2] == ("ProjectTask", 19769)
    assert hosts[3] == ("ProjectTask", 20000)
    assert hosts[4] == (None, None)

    out_of_scope = next(item for item in planned if item.journal_id == 4)
    assert out_of_scope.outcome is NoteOutcome.NO_HOST
    assert out_of_scope.detail  # never silent: the report needs a reason


def test_relation_faturamento_is_counted_once():
    """A tracker-15 partner arrives through relations, a descendant through the
    tree. Both paths feeding the same issue must not double the notes."""
    partner = issue(17539, 15, [journal(9)])
    root = tree_with_children(children=[], root_journals=[])

    planned = plan_notes(plan_tree(root), [partner], root_issue_id=16467)
    assert [item.journal_id for item in planned] == [9]
    assert planned[0].host_itemtype == "ProjectTask"
    assert planned[0].host_redmine_id == 17539


def test_missing_journals_key_is_not_a_crash():
    """Verified trap: Redmine omits the key entirely on some issues."""
    root = TreeNode(issue=issue(16467, 14))  # no 'journals' key at all
    assert plan_notes(plan_tree(root), [], root_issue_id=16467) == []
    assert journals_of({"journals": None}) == []


def test_unreadable_child_is_still_visible():
    """A child whose GET failed may have carried notes; the report must not
    imply it had none."""
    root = tree_with_children(children=[], root_journals=[])
    planned = plan_notes(
        plan_tree(root), [], root_issue_id=16467, tree_failures=[(18888, "erro")]
    )
    assert len(planned) == 1
    assert planned[0].issue_id == 18888
    assert planned[0].outcome is NoteOutcome.NO_HOST


# -- classification ---------------------------------------------------------


def test_history_only_entry_is_classified_as_such_not_as_a_note():
    """A status change carries no text. It is not a loss, and it must not be
    counted as one - RDM 16467 has 63 of these against 14 real notes."""
    root = tree_with_children(children=[], root_journals=[journal(1, text="")])
    planned = plan_notes(plan_tree(root), [], root_issue_id=16467)
    assert planned[0].outcome is NoteOutcome.HISTORY_ONLY
    assert not planned[0].has_text


def test_history_only_wins_over_no_host():
    """An out-of-scope issue's history entries were never going to be written.

    Calling them NO_HOST would inflate the "lost because out of scope" figure
    with entries that carry nothing to lose.
    """
    root = tree_with_children(
        children=[TreeNode(issue=issue(30000, 39, [journal(4, text="   ")]))],
        root_journals=[],
    )
    planned = plan_notes(plan_tree(root), [], root_issue_id=16467)
    assert planned[0].outcome is NoteOutcome.HISTORY_ONLY


def test_private_note_is_migrated_and_flagged():
    root = tree_with_children(children=[], root_journals=[journal(1, private=True)])
    planned = plan_notes(plan_tree(root), [], root_issue_id=16467)
    assert planned[0].private
    assert planned[0].outcome is NoteOutcome.PLANNED
    assert messages.NOTE_PRIVATE in main._notepad_content(planned[0])


def test_attachment_names_come_from_the_journal_details():
    """The note -> file association exists ONLY in the journal details; the
    issue's own attachments list says nothing about which note brought what."""
    root = tree_with_children(
        children=[],
        root_journals=[journal(1, attachments=[(34184, "TAP.xlsx"), (34185, "Espelho.pdf")])],
    )
    planned = plan_notes(plan_tree(root), [], root_issue_id=16467)
    assert planned[0].attachment_names == ["TAP.xlsx", "Espelho.pdf"]
    assert "TAP.xlsx" in main._notepad_content(planned[0])


def test_skip_flag_keeps_the_notes_in_the_report():
    """--skip-notes must not make notes disappear, only unmigrated."""
    root = tree_with_children(children=[], root_journals=[journal(1)])
    planned = plan_notes(plan_tree(root), [], root_issue_id=16467, skip=True)
    assert planned[0].outcome is NoteOutcome.SKIPPED_BY_FLAG


def test_summarise_closes_its_arithmetic():
    root = tree_with_children(
        children=[TreeNode(issue=issue(30000, 39, [journal(4)]))],
        root_journals=[journal(1), journal(2, text="")],
    )
    counts = summarise(plan_notes(plan_tree(root), [], root_issue_id=16467))
    assert counts["total"] == 3
    assert counts["pending"] + counts["done"] + counts["skipped"] + counts["failed"] == 3
    assert counts["with_text"] == 2
    assert counts["history_only"] == 1


# -- the reporting contract -------------------------------------------------


def minimal_plan(notes: list) -> ProjectPlan:
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
        notes_planned=notes,
    )


def hosted_note(journal_id: int, **extra) -> PlannedNote:
    defaults = dict(
        journal_id=journal_id,
        issue_id=16467,
        author="henrique.sokal",
        created_on="2026-01-12T17:48:59Z",
        text="Reunião 12-01",
        host_itemtype="Project",
        host_redmine_id=16467,
        host_label="Projeto RDM 16467",
    )
    defaults.update(extra)
    return PlannedNote(**defaults)


def test_notes_do_not_touch_the_field_arithmetic():
    """Section 7 counts source FIELDS. Notes must not appear in that total."""
    notes = [hosted_note(1)]
    without = Reporter(minimal_plan([])).render()
    with_notes = Reporter(minimal_plan(notes)).render()

    def integrity_block(text: str) -> str:
        start = text.index("7. CONFER")
        return text[start : text.index("8. ANEXOS")]

    assert integrity_block(without) == integrity_block(with_notes)
    assert len(minimal_plan(notes).all_records()) == len(minimal_plan([]).all_records())


def test_notes_do_not_touch_the_attachment_arithmetic():
    """Section 8 counts FILES. A note naming a file is still not a file."""
    notes = [hosted_note(1, attachment_names=["TAP.xlsx"])]
    without = Reporter(minimal_plan([])).render()
    with_notes = Reporter(minimal_plan(notes)).render()

    def attachment_block(text: str) -> str:
        start = text.index("8. ANEXOS")
        return text[start : text.index("9. NOTAS")]

    assert attachment_block(without) == attachment_block(with_notes)


def test_section_9_closes_its_own_arithmetic():
    notes = [
        hosted_note(1),
        PlannedNote(journal_id=2, issue_id=30000, text="nota perdida",
                    outcome=NoteOutcome.NO_HOST, host_label="RDM 30000",
                    detail="fora do escopo"),
        hosted_note(3, outcome=NoteOutcome.FAILED_WRITE),
        PlannedNote(journal_id=4, issue_id=16467, text="",
                    outcome=NoteOutcome.HISTORY_ONLY,
                    host_itemtype="Project", host_redmine_id=16467,
                    host_label="Projeto RDM 16467"),
    ]
    text = Reporter(minimal_plan(notes)).render()
    assert "1 + 0 + 2 + 1 = 4" in text
    assert "nenhuma nota foi descartada" in text
    # The unmigrated note keeps its own block and its reason.
    assert "NÃO MIGRADAS" in text
    assert "fora do escopo" in text
    # History entries are aggregated, never listed one per line.
    assert "1 entrada(s) de histórico sem texto" in text


def test_history_only_issue_still_reaches_the_report():
    """An issue whose journals are ALL history-only appears in no note group,
    so its count would otherwise vanish. That is the common case."""
    notes = [
        PlannedNote(journal_id=9, issue_id=19769, text="",
                    outcome=NoteOutcome.HISTORY_ONLY,
                    host_itemtype="ProjectTask", host_redmine_id=19769,
                    host_label="Tarefa RDM 19769"),
    ]
    text = Reporter(minimal_plan(notes)).render()
    assert "Tarefa RDM 19769" in text
    assert "1 entrada(s) de histórico sem texto" in text
    assert "0 + 0 + 1 + 0 = 1" in text


# -- deduplication marker ---------------------------------------------------


def test_notepad_rows_ask_for_a_full_range():
    """GLPI returns 15 rows without an explicit range.

    Measured on project 1277 for Document_Item; the same truncation on the
    Notas tab would make a re-run duplicate every note past the fifteenth.
    """
    seen = {}

    class Recorder(GlpiClient):
        def __init__(self):  # no session, no network
            pass

        def _request(self, _method, path, params=None, **_kwargs):
            seen[path] = params
            return []

    Recorder().notepad_rows("Project", 1277)
    assert seen["/Project/1277/Notepad"]["range"] == SEARCH_FETCH_RANGE


def test_note_marker_is_the_first_line_and_line_anchored():
    """rdmnote:1559 must never match rdmnote:155016."""
    content = main._notepad_content(hosted_note(155016))
    first_line = content.splitlines()[0]

    assert first_line == f"{NOTE_MARKER_PREFIX}155016"
    assert _has_exact_marker(content, f"{NOTE_MARKER_PREFIX}155016")
    assert not _has_exact_marker(content, f"{NOTE_MARKER_PREFIX}1559")


def test_note_text_is_copied_verbatim():
    """Migrated data is never rewritten - only the header is ours."""
    note = hosted_note(1, text="Linha 1\r\n- item com acento: ação\r\n")
    assert main._notepad_content(note).endswith("Linha 1\r\n- item com acento: ação\r\n")


# -- apply degradation ------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.records = []

    def lookup(self, *_args, **_kwargs):
        return None

    def record(self, redmine_id, glpi_id, itemtype, **kwargs):
        self.records.append((redmine_id, glpi_id, itemtype))


class FakeGlpi:
    """Just enough of GlpiClient for apply_notes."""

    def __init__(self, failing_journals: set[int] | None = None, existing=None):
        self.failing_journals = failing_journals or set()
        self.existing = existing or {}
        self.written = []
        self._next_id = 40

    def find_notepad_by_marker(self, _itemtype, _items_id, journal_id):
        return self.existing.get(journal_id)

    def get_item(self, _itemtype, _item_id):
        return None

    def create_notepad(self, itemtype, items_id, content):
        marker = content.splitlines()[0]
        if int(marker.removeprefix(NOTE_MARKER_PREFIX)) in self.failing_journals:
            raise GlpiError("ERROR_GLPI_ADD")
        self._next_id += 1
        self.written.append((itemtype, items_id, content))
        return self._next_id


def test_one_failed_note_does_not_stop_the_rest():
    notes = [hosted_note(1), hosted_note(2)]
    plan = minimal_plan(notes)
    plan.glpi_ids = {16467: 1265}
    glpi = FakeGlpi(failing_journals={1})
    store = FakeStore()

    main.apply_notes(glpi, plan, store)

    assert notes[0].outcome is NoteOutcome.FAILED_WRITE
    assert notes[1].outcome is NoteOutcome.WRITTEN
    assert notes[1].glpi_notepad_id
    assert plan.notes  # the failure reached the report
    assert store.records == [(2, notes[1].glpi_notepad_id, ITEMTYPE_NOTEPAD)]


def test_existing_marker_dedups_without_writing():
    """The proof that losing migration.db cannot duplicate notes: GLPI is asked
    first, and its answer alone is enough to skip the write."""
    notes = [hosted_note(1)]
    plan = minimal_plan(notes)
    plan.glpi_ids = {16467: 1265}
    glpi = FakeGlpi(existing={1: {"id": 77}})
    store = FakeStore()

    main.apply_notes(glpi, plan, store)

    assert notes[0].outcome is NoteOutcome.DEDUP_GLPI
    assert notes[0].glpi_notepad_id == 77
    assert glpi.written == []
    assert store.records == [(1, 77, ITEMTYPE_NOTEPAD)]


def test_note_whose_host_was_never_created_is_reported():
    """A task whose POST failed leaves its notes homeless. That is not a note
    problem and the report has to say which one it is."""
    notes = [hosted_note(1, host_redmine_id=19769, host_itemtype="ProjectTask")]
    plan = minimal_plan(notes)
    plan.glpi_ids = {}  # the host never made it into GLPI
    store = FakeStore()

    main.apply_notes(FakeGlpi(), plan, store)

    assert notes[0].outcome is NoteOutcome.NO_HOST
    assert notes[0].detail
    assert plan.notes

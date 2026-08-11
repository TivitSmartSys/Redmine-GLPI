"""Resetting a migration must clear both caches, and only when it is safe to.

Two failure modes drive these tests. Deleting a live project's marker would
strip the only dedup guard the migration has, so an ACTIVE host stops the run
cold. And clearing the GLPI marker while the SQLite rows survive produces the
quiet disaster: the next run is allowed through, then apply_plan skips every
task via store.lookup and creates a project with nothing in it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import reset_migration  # noqa: E402
from clients.errors import GlpiError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from reset_migration import ACTIVE, ORPHAN, TRASHED, classify_marker  # noqa: E402
from store.db import MigrationStore  # noqa: E402

# The live shape of 2026-08-07: project 16467 with three tasks and two
# Faturamentos, each Faturamento holding a ProjectTask row AND a container row.
TREE_16467 = [
    (16467, 1275, "Project", None),
    (19155, 14100, "ProjectTask", 16467),
    (19769, 14101, "ProjectTask", 16467),
    (20230, 14102, "ProjectTask", 16467),
    (17539, 14103, "ProjectTask", None),
    (17539, 4, "PluginFieldsProjecttaskfaturamento", None),
    (20322, 14104, "ProjectTask", None),
    (20322, 5, "PluginFieldsProjecttaskfaturamento", None),
]
OTHER_TREE = [(20238, 1265, "Project", None), (20292, 14093, "ProjectTask", 20156)]


@pytest.fixture()
def store(tmp_path):
    with MigrationStore(tmp_path / "migration.db") as db:
        for redmine_id, glpi_id, itemtype, parent in TREE_16467 + OTHER_TREE:
            db.record(redmine_id, glpi_id, itemtype, parent_redmine_id=parent)
        yield db


def probe(project: dict | None, raises: Exception | None = None):
    """classify_marker against a canned GET /Project reply."""
    client = GlpiClient.__new__(GlpiClient)  # no session, no network

    def fake_request(method, path, **kwargs):
        assert method == "GET" and path.startswith("/Project/")
        if raises is not None:
            raise raises
        if project is None:
            raise GlpiError("Erro HTTP 404 em GET /Project/1275: nada")
        return project

    client._request = fake_request  # type: ignore[method-assign]
    return classify_marker(client, {"id": 934, "items_id": 1275})


# -- marker classification ------------------------------------------------


def test_live_project_is_active_and_must_not_be_touched():
    marker = probe({"id": 1275, "is_deleted": 0})
    assert marker.state == ACTIVE
    assert marker.deletable is False


def test_missing_project_leaves_the_marker_orphaned():
    assert probe(None).state == ORPHAN


def test_project_in_the_trash_counts_as_orphaned():
    """GLPI soft-deletes: the row survives, and so would the block."""
    assert probe({"id": 1275, "is_deleted": 1}).state == TRASHED
    assert probe({"id": 1275, "is_deleted": "1"}).state == TRASHED


def test_marker_without_a_host_id_is_orphaned():
    client = GlpiClient.__new__(GlpiClient)
    client._request = lambda *a, **k: pytest.fail("must not ask GLPI")  # type: ignore
    assert classify_marker(client, {"id": 934, "items_id": 0}).state == ORPHAN


def test_a_real_glpi_failure_is_not_mistaken_for_a_missing_project():
    """Only 404 means absent; anything else must surface, not delete a marker."""
    with pytest.raises(GlpiError):
        probe(None, raises=GlpiError("Erro HTTP 500 em GET /Project/1275: boom"))


# -- the REST calls behind the reset --------------------------------------


def test_delete_item_purges_instead_of_binning():
    """A binned container row keeps its rdmfield and would still block."""
    client = GlpiClient.__new__(GlpiClient)
    seen: dict = {}

    def fake_request(method, path, **kwargs):
        seen.update(method=method, path=path, body=kwargs.get("json_body"))
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    client.delete_item("PluginFieldsProjectcamposadicionais", 934)

    assert seen["method"] == "DELETE"
    assert seen["path"] == "/PluginFieldsProjectcamposadicionais/934"
    assert seen["body"] == {"input": {"id": 934}, "force_purge": True}


def test_get_item_reads_absence_as_none_and_keeps_real_errors():
    client = GlpiClient.__new__(GlpiClient)

    client._request = lambda *a, **k: {"id": 1275, "is_deleted": 0}  # type: ignore
    assert client.get_item("Project", 1275)["id"] == 1275

    def gone(*_a, **_k):
        raise GlpiError("Erro HTTP 400 em GET /Project/1275: ERROR_ITEM_NOT_FOUND")

    client._request = gone  # type: ignore[method-assign]
    assert client.get_item("Project", 1275) is None


# -- the local map --------------------------------------------------------


def test_delete_for_ids_removes_the_whole_tree_and_nothing_else(store):
    removed = store.delete_for_ids([16467, 19155, 19769, 20230, 17539, 20322])
    assert removed == len(TREE_16467)
    assert store.lookup(16467, "Project") is None
    assert store.lookup(17539, "PluginFieldsProjecttaskfaturamento") is None
    # The other tree is untouched - a reset is per issue, never a wipe.
    assert store.lookup(20238, "Project") is not None
    assert store.lookup(20292, "ProjectTask") is not None


def test_delete_for_ids_takes_every_itemtype_of_one_issue(store):
    """A Faturamento issue owns two rows; leaving one behind still skips it."""
    assert store.delete_for_ids([17539]) == 2
    assert store.lookup(17539, "ProjectTask") is None
    assert store.lookup(17539, "PluginFieldsProjecttaskfaturamento") is None


def test_entries_for_ids_previews_exactly_what_delete_would_take(store):
    ids = [redmine_id for redmine_id, *_ in TREE_16467]
    preview = store.entries_for_ids(ids)
    assert len(preview) == len(TREE_16467)
    assert 20238 not in {entry.redmine_id for entry in preview}


def test_ids_absent_from_the_map_are_harmless(store):
    """Out-of-scope children are passed in on purpose and own no rows."""
    assert store.delete_for_ids([999999]) == 0
    assert store.entries_for_ids([]) == []


def test_entries_for_root_no_longer_leaks_other_trees(store):
    """The predicate used to be unbound and matched every parented row."""
    found = store.entries_for_root(16467)
    assert {entry.redmine_id for entry in found} == {16467, 19155, 19769, 20230}


def test_chunking_keeps_large_trees_in_one_call(tmp_path):
    """Faturamento trees run to thousands of issues; SQLite binds ~999 at once."""
    with MigrationStore(tmp_path / "big.db") as db:
        for redmine_id in range(1, 1201):
            db.record(redmine_id, redmine_id + 10000, "ProjectTask")
        assert len(db.entries_for_ids(range(1, 1201))) == 1200
        assert db.delete_for_ids(range(1, 1201)) == 1200


# -- the write gate -------------------------------------------------------


@pytest.mark.parametrize("answer", ["", "não", "nao", "no", "s im", "yep", "1"])
def test_anything_but_the_word_is_a_cancel(monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    assert reset_migration.confirm_reset() is False


@pytest.mark.parametrize("answer", sorted(reset_migration.messages.APPLY_CONFIRM_ACCEPT))
def test_reset_accepts_the_same_words_as_the_migration(monkeypatch, answer):
    monkeypatch.setattr("builtins.input", lambda _prompt: f"  {answer.upper()}  ")
    assert reset_migration.confirm_reset() is True


def test_no_terminal_is_a_cancel(monkeypatch):
    def no_stdin(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    assert reset_migration.confirm_reset() is False

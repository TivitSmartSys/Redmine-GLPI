"""Reading a whole itemtype must not stop at a page boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.glpi import GlpiClient  # noqa: E402


def client_returning(pages):
    """A GlpiClient with no session, answering canned pages in order."""
    client = GlpiClient.__new__(GlpiClient)
    calls = []

    def fake_request(method, path, params=None, **kwargs):
        calls.append((path, (params or {}).get("range")))
        return pages.pop(0) if pages else []

    client._request = fake_request
    client.calls = calls
    return client


def test_iter_all_rows_follows_every_page():
    full = [{"id": n} for n in range(200)]
    tail = [{"id": 200 + n} for n in range(30)]
    client = client_returning([full, tail])

    rows = client.iter_all_rows("PluginFieldsProjectcamposadicionaisprojeto")

    assert len(rows) == 230
    assert client.calls[0][1] == "0-199"
    assert client.calls[1][1] == "200-399"


def test_iter_all_rows_stops_on_short_page():
    client = client_returning([[{"id": 1}, {"id": 2}]])

    rows = client.iter_all_rows("Project")

    assert len(rows) == 2
    assert len(client.calls) == 1


def test_iter_all_rows_returns_empty_when_itemtype_has_no_rows():
    client = client_returning([[]])

    assert client.iter_all_rows("Project") == []


def test_iter_all_rows_survives_a_page_longer_than_requested():
    """A server that ignores `range` must not spin the loop forever."""
    oversized = [{"id": n} for n in range(250)]
    client = client_returning([oversized, []])

    rows = client.iter_all_rows("Project", page_size=200)

    assert len(rows) == 250
    assert client.calls[1][1] == "250-449"


from batch.selection import (  # noqa: E402
    REDMINE_PROJECTS,
    candidate_roots,
    migrated_markers,
    pending_roots,
)


class MarkerGlpi:
    def __init__(self, rows):
        self._rows = rows

    def iter_all_rows(self, itemtype, page_size=200):
        return list(self._rows)


class FakeRedmine:
    def __init__(self, issues):
        self._issues = issues

    def iter_issues(self, tracker_id, page_size=100):
        for issue in self._issues:
            if issue["tracker"]["id"] == tracker_id:
                yield issue


def issue(iid, tracker=14, parent=None):
    row = {"id": iid, "tracker": {"id": tracker}}
    if parent:
        row["parent"] = {"id": parent}
    return row


def test_markers_are_read_in_one_bulk_pass_and_only_numeric_ones_count():
    glpi = MarkerGlpi([
        {"items_id": 100, "rdmfield": "17343"},
        {"items_id": 101, "rdmfield": "RDM123"},
        {"items_id": 102, "rdmfield": ""},
        {"items_id": 103, "rdmfield": "  20438  "},
    ])

    assert migrated_markers(glpi) == {17343, 20438}


def test_candidates_are_parentless_roots_of_that_tracker_only():
    redmine = FakeRedmine([
        issue(1), issue(2, parent=1), issue(3), issue(4, tracker=42),
    ])

    assert candidate_roots(redmine, 14) == [1, 3]


def test_pending_subtracts_the_markers():
    glpi = MarkerGlpi([{"items_id": 100, "rdmfield": "3"}])
    redmine = FakeRedmine([issue(1), issue(2), issue(3)])

    assert pending_roots(glpi, redmine, 14) == [1, 2]


def test_limit_takes_the_first_n_only():
    glpi = MarkerGlpi([])
    redmine = FakeRedmine([issue(1), issue(2), issue(3)])

    assert pending_roots(glpi, redmine, 14, limit=2) == [1, 2]


def test_the_three_redmine_projects_map_to_their_root_trackers():
    assert REDMINE_PROJECTS == {
        "projetos-telecom": 14,
        "hydro": 42,
        "operacao-cemig": 39,
    }


def test_limit_zero_selects_nothing_rather_than_everything():
    """`--limit 0` must mean none, not all. Truthiness would invert this."""
    glpi = MarkerGlpi([])
    redmine = FakeRedmine([issue(1), issue(2), issue(3)])

    assert pending_roots(glpi, redmine, 14, limit=0) == []


def test_limit_slices_after_the_subtraction_not_before():
    """Slicing first would drop pending roots to make room for migrated ones."""
    glpi = MarkerGlpi([{"items_id": 100, "rdmfield": "1"},
                       {"items_id": 101, "rdmfield": "2"}])
    redmine = FakeRedmine([issue(1), issue(2), issue(3), issue(4)])

    # 1 and 2 are already migrated; the first two PENDING roots are 3 and 4.
    assert pending_roots(glpi, redmine, 14, limit=2) == [3, 4]

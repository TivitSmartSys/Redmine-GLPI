"""C2 (fix wave 2026-08-27): find_by_rdmfield must not be truncated to the
first 15 rows.

CLAUDE.md's documented trap: without an explicit `range`, GLPI answers a list
search with the first 15 rows only. searchText[rdmfield] is a substring match,
so marker "1240" also matches "11240" and "12400".."12409" - on a container-15
table growing towards 5627 rows the exact-match row can sit outside that
15-row window, check_already_migrated then returns None and the project is
migrated a second time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.glpi import GlpiClient  # noqa: E402
from config.settings import SEARCH_FETCH_RANGE  # noqa: E402


class TruncatingRecorder(GlpiClient):
    """Stands in for the real GLPI: truncates to 15 rows unless `range` is
    passed, exactly like the documented server behaviour."""

    def __init__(self, rows):
        # no session, no network - bypass GlpiClient.__init__ entirely
        self._rows = rows
        self.seen_params = []

    def _request(self, _method, _path, params=None, **_kwargs):
        self.seen_params.append(params)
        if params and "range" in params:
            return list(self._rows)
        return list(self._rows)[:15]


def test_full_range_is_requested():
    glpi = TruncatingRecorder([])
    glpi.find_by_rdmfield(1240)

    assert glpi.seen_params[0]["range"] == SEARCH_FETCH_RANGE


def test_exact_match_beyond_the_first_15_substring_hits_is_still_found():
    # 19 substring-only hits ("12400".."12418"), then the exact match last -
    # position 20, well past the server's un-ranged 15-row default.
    rows = [{"id": i, "rdmfield": f"1240{i}"} for i in range(19)]
    rows.append({"id": 999, "rdmfield": "1240"})
    glpi = TruncatingRecorder(rows)

    result = glpi.find_by_rdmfield(1240)

    assert [row["id"] for row in result] == [999]


def test_without_full_range_the_exact_match_would_be_missed():
    """Proves the fixture itself reproduces the real trap: an un-ranged read
    truncated to 15 rows never reaches the exact match sitting at position 20."""
    rows = [{"id": i, "rdmfield": f"1240{i}"} for i in range(19)]
    rows.append({"id": 999, "rdmfield": "1240"})
    glpi = TruncatingRecorder(rows)

    truncated = glpi._search("PluginFieldsProjectcamposadicionaisprojeto",
                              {"searchText[rdmfield]": "1240"}, full_range=False)

    assert all(row["id"] != 999 for row in truncated)

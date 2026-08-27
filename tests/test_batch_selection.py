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

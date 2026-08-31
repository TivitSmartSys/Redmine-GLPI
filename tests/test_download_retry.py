"""A dropped connection must not cost a file; a 404 must not cost three tries.

Measured 2026-08-31 during the HYDRO batch: RDM 19314 lost a 48 KB attachment
to `RemoteDisconnected('Remote end closed connection without response')`, and
the very same file downloaded on the first attempt minutes later. One drop in
roughly 550 downloads. Telecom is about thirty times that volume, so without a
retry a few dozen perfectly good files land on the manual-upload list because
the network blinked.

The distinction this pins is the whole point: a CONNECTION failure is worth
retrying, an HTTP STATUS failure is not. A 404 means Redmine no longer has the
file on disk - real data, already seen on RDM 1240's 77 attachments - and
retrying it three times only makes the run slower and the report noisier.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
import requests  # noqa: E402

from clients.errors import RedmineError  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, body=b"x" * 2048):
        self.status_code = status_code
        self.text = "erro" if status_code >= 400 else ""
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_content(self, chunk_size=8192):
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


def client_with(responses):
    """A RedmineClient whose session yields the given responses in order.

    An entry that is an exception instance is raised instead of returned.
    """
    client = RedmineClient.__new__(RedmineClient)
    client._timeout = 1
    calls = []

    class FakeSession:
        def get(self, url, **kwargs):
            calls.append(url)
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    client._session = FakeSession()
    client.calls = calls
    return client


@pytest.fixture(autouse=True)
def no_real_sleeping(monkeypatch):
    """Keep the backoff honest but instant."""
    slept = []
    monkeypatch.setattr("clients.redmine.time.sleep", lambda s: slept.append(s))
    return slept


def test_a_dropped_connection_is_retried_and_succeeds(tmp_path):
    client = client_with(
        [requests.ConnectionError("Remote end closed connection"), FakeResponse()]
    )

    written = client.download_attachment("http://x/1", tmp_path / "f.xlsx")

    assert written == 2048
    assert len(client.calls) == 2, "the second attempt is what saves the file"


def test_the_retry_waits_between_attempts(tmp_path, no_real_sleeping):
    client = client_with(
        [requests.ConnectionError("boom"), requests.ConnectionError("boom"), FakeResponse()]
    )

    client.download_attachment("http://x/1", tmp_path / "f.xlsx")

    assert no_real_sleeping, "a retry with no pause just hits a busy host again"
    assert no_real_sleeping == sorted(no_real_sleeping), "the backoff must grow"


def test_a_404_is_not_retried(tmp_path):
    client = client_with([FakeResponse(status_code=404)])

    with pytest.raises(RedmineError):
        client.download_attachment("http://x/gone", tmp_path / "f.xlsx")

    assert len(client.calls) == 1, (
        "a 404 is real data - Redmine no longer has the file - not a hiccup"
    )


def test_every_attempt_failing_still_raises(tmp_path):
    client = client_with([requests.ConnectionError("boom")] * 3)

    with pytest.raises(RedmineError):
        client.download_attachment("http://x/1", tmp_path / "f.xlsx")

    assert len(client.calls) == 3, "three attempts, then report it as before"


def test_a_retry_does_not_append_to_a_half_written_file(tmp_path):
    """The first attempt may have written bytes before the connection died."""
    dest = tmp_path / "f.xlsx"
    dest.write_bytes(b"garbage from a previous attempt")
    client = client_with([requests.ConnectionError("boom"), FakeResponse()])

    written = client.download_attachment("http://x/1", dest)

    assert written == 2048
    assert dest.stat().st_size == 2048, "the file must be the download, not a concatenation"

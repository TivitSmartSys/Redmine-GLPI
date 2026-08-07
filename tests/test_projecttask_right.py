"""Preflight must see the right that hid behind a successful read.

On 2026-08-07 the Fields-plugin read check passed, the migration ran, and every
container-26 row was refused afterwards: inserting a container row on a
ProjectTask needs UPDATE on `projecttask`, which the API profile had for
`project` only. These tests pin the three answers the probe may give, because
the difference between "missing" and "unknown" is what decides whether the user
sees a warning or nothing at all - a guess must never be reported as a fact.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clients.errors import GlpiError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402


def probe(payload, raises: Exception | None = None):
    """Run can_write_projecttask_containers against a canned GET reply."""
    client = GlpiClient.__new__(GlpiClient)  # no session, no network

    def fake_request(method, path, **kwargs):
        assert (method, path) == ("GET", "/getActiveProfile")
        if raises is not None:
            raise raises
        return payload

    client._request = fake_request  # type: ignore[method-assign]
    return client.can_write_projecttask_containers()


# The live values of 2026-08-07: project 1151 had UPDATE(2), projecttask 1145
# did not, and those two bits are the whole story behind the refused rows.
RIGHT_WITHOUT_UPDATE = 1145
RIGHT_WITH_UPDATE = 1151


def test_missing_update_bit_is_reported_as_missing():
    assert probe({"active_profile": {"projecttask": RIGHT_WITHOUT_UPDATE}}) is False


def test_update_bit_present_is_reported_as_allowed():
    assert probe({"active_profile": {"projecttask": RIGHT_WITH_UPDATE}}) is True


def test_profile_returned_unwrapped_is_still_read():
    """GLPI has answered both with and without the active_profile envelope."""
    assert probe({"projecttask": RIGHT_WITH_UPDATE}) is True


def test_unknown_answers_are_none_never_a_warning():
    """None means "could not tell" - preflight stays silent instead of crying wolf."""
    assert probe({"active_profile": {}}) is None            # key absent
    assert probe({"active_profile": {"projecttask": None}}) is None  # null value
    assert probe({"active_profile": "nonsense"}) is None    # unexpected shape
    assert probe([]) is None                                # not a mapping
    assert probe(None, raises=GlpiError("sem sessão")) is None  # endpoint failed

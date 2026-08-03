"""The write gate is the safety rule the panel must not weaken.

The CLI writes only after --apply plus a typed "sim". The panel has to be
exactly as strict: the browser may not decide, a wrong word must not write, and
a job that nobody confirms must let go of the GLPI session instead of hanging.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from report import messages  # noqa: E402
from web import jobs as jobs_module  # noqa: E402
from web.jobs import Job, JobBusy, JobManager  # noqa: E402


def awaiting_job() -> tuple[Job, threading.Thread]:
    """A job parked on the confirmation gate, as during a real --apply run."""
    job = Job(kind="migration", label="20238")
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(ok=job.wait_for_confirmation()))
    thread.start()
    deadline = time.monotonic() + 5
    while job.state != "awaiting_confirm" and time.monotonic() < deadline:
        time.sleep(0.001)
    assert job.state == "awaiting_confirm"
    job._result = result  # type: ignore[attr-defined]
    return job, thread


@pytest.mark.parametrize("answer", sorted(messages.APPLY_CONFIRM_ACCEPT))
def test_accepted_words_release_the_gate(answer):
    job, thread = awaiting_job()
    assert job.confirm(answer) is True
    thread.join(timeout=5)
    assert job._result["ok"] is True


@pytest.mark.parametrize("answer", ["", "não", "nao", "no", "s im", "yep", "1", None])
def test_anything_else_is_a_cancel(answer):
    """Same as the CLI prompt: a wrong answer cancels, it does not retry."""
    job, thread = awaiting_job()
    assert job.confirm(answer) is False
    thread.join(timeout=5)
    assert job._result["ok"] is False


def test_case_and_whitespace_are_tolerated():
    job, thread = awaiting_job()
    assert job.confirm("  SIM  ") is True
    thread.join(timeout=5)
    assert job._result["ok"] is True


def test_confirm_is_ignored_when_no_gate_is_open():
    """A stray confirm must never be able to start a write on its own."""
    job = Job(kind="migration", label="20238")
    assert job.state == "running"
    assert job.confirm("sim") is False


def test_cancel_releases_without_writing():
    job, thread = awaiting_job()
    job.cancel()
    thread.join(timeout=5)
    assert job._result["ok"] is False


def test_unconfirmed_job_times_out_instead_of_holding_the_session(monkeypatch):
    monkeypatch.setattr(jobs_module, "CONFIRM_TIMEOUT_SECONDS", 0.2)
    job = Job(kind="migration", label="20238")
    assert job.wait_for_confirmation() is False
    assert any(
        messages.UI_CONFIRM_EXPIRED.split("\n")[0][:30] in str(event.data)
        for event in job.log._events
        if event.type == "log"
    )


def test_only_one_job_at_a_time():
    manager = JobManager(settings=None, mapping={}, db_path=":memory:")
    started = threading.Event()
    release = threading.Event()

    def slow(job):
        started.set()
        release.wait(timeout=5)

    manager._register(Job(kind="migration", label="1"), slow)
    started.wait(timeout=5)
    with pytest.raises(JobBusy):
        manager._register(Job(kind="migration", label="2"), slow)
    release.set()


def test_secrets_never_reach_the_event_stream():
    messages.register_secrets(("super-secret-token",))
    job = Job(kind="migration", label="20238")
    job.emit_line("Authorization: super-secret-token")
    job.publish_report("token=super-secret-token", {})
    payloads = [str(event.data) for event in job.log._events]
    assert all("super-secret-token" not in payload for payload in payloads)
    assert "super-secret-token" not in job.report_text

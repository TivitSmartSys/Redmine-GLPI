"""The panel's batch view: same engine as migrate_batch.py, same write gate.

Nothing here re-tests the migration. `batch/runner.py` owns the loop and
`tests/test_batch_runner.py` covers it; these tests cover the three things the
web layer adds and the CLI does not have - a queue built before the operator
confirms, a progress feed that does not go through stdout, and routes that let
a browser reach files on disk.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from report import messages  # noqa: E402
from store.batch import STATE_FAILED, STATE_OK, BatchLedger  # noqa: E402
from web import jobs as jobs_module  # noqa: E402
from web.jobs import Job, _EventLog  # noqa: E402

REQUIRED_ENV = {
    "REDMINE_URL": "https://redmine.invalid",
    "REDMINE_API_KEY": "chave-redmine-de-teste",
    "GLPI_URL": "https://glpi.invalid/apirest.php",
    "GLPI_USER_TOKEN": "token-usuario-de-teste",
    "GLPI_APP_TOKEN": "token-app-de-teste",
}


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """A Flask test client whose settings come from the environment, not .env.

    load_settings() calls load_dotenv(override=False), so these win over the
    operator's real .env and the suite stays reproducible on any machine.
    """
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    from web.server import create_app

    app = create_app(db_path=str(tmp_path / "b.db"), reports_dir=str(tmp_path / "reports"))
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        test_client.application = app
        yield test_client


# -- the bounded event log ---------------------------------------------------
#
# main.py prints from 65 sites. At 5451 roots an unbounded log is hundreds of
# thousands of events pinned in the process for the life of the run.


def test_old_log_lines_are_dropped_but_structure_survives():
    log = _EventLog(max_log_events=10)
    log.append("batch_queue", {"count": 3})
    for number in range(200):
        log.append("log", f"linha {number}")
    log.append("done", {"state": "done"})
    log.close()

    events = list(log.follow(0))
    kinds = [event.type for event in events if event is not None]

    assert kinds[0] == "batch_queue", "structural events are never dropped"
    assert "done" in kinds
    assert kinds.count("log") <= 40, kinds.count("log")
    # The tail is what an operator needs; the head is what gets dropped.
    logs = [event.data for event in events if event is not None and event.type == "log"]
    assert logs[-1] == "linha 199"


def test_a_follower_survives_the_gap_left_by_dropped_events():
    """Indices stop being list positions once trimming starts.

    A browser reconnecting with ?from=<n> must still get the events that are
    left, not an empty stream and not an IndexError.
    """
    log = _EventLog(max_log_events=5)
    for number in range(100):
        log.append("log", f"linha {number}")
    log.append("done", {"state": "done"})
    log.close()

    received = [event for event in log.follow(3) if event is not None]

    assert received, "a stale cursor must still deliver what is retained"
    assert received[-1].type == "done"
    # Indices are monotonic even with holes, so the next cursor is well defined.
    assert [event.index for event in received] == sorted(
        event.index for event in received
    )


def test_the_log_is_unbounded_by_default():
    """The single-issue path is short and must keep every line it always had."""
    log = _EventLog()
    for number in range(500):
        log.append("log", f"linha {number}")
    log.close()

    assert len([event for event in log.follow(0) if event is not None]) == 500


# -- routes ------------------------------------------------------------------


def test_an_unknown_project_is_refused_before_any_session_opens(client):
    response = client.post("/api/batch", json={"project": "nao-existe", "mode": "dry"})

    assert response.status_code == 400
    assert response.get_json()["error"] == messages.UI_BATCH_PROJECT_INVALID


def test_a_negative_limit_is_refused(client):
    response = client.post(
        "/api/batch", json={"project": "hydro", "mode": "dry", "limit": -1}
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == messages.UI_BATCH_LIMIT_INVALID


def test_resume_refuses_a_run_the_ledger_never_started(client):
    """An unknown id yields an empty queue, which reads exactly like success.

    The CLI learned this on 2026-08-27 (BATCH_RUN_NOT_FOUND); the panel must
    not re-introduce it.
    """
    response = client.post("/api/batch/resume", json={"run_id": "nao-existe"})

    assert response.status_code == 404
    assert "nao-existe" in response.get_json()["error"]


def test_runs_are_listed_newest_first_with_their_counts(client, tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2])
        ledger.mark(run, 1, STATE_OK)

    body = client.get("/api/batch/runs").get_json()

    assert [entry["run_id"] for entry in body] == [run]
    assert body[0]["label"] == "hydro"
    assert body[0]["counts"][STATE_OK] == 1
    assert body[0]["resumable"] == 1


def test_an_item_report_is_served_from_the_run_directory(client, tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
    directory = tmp_path / "reports" / run
    directory.mkdir(parents=True)
    (directory / "RDM77.txt").write_text("relatório do item", encoding="utf-8")

    response = client.get(f"/api/batch/runs/{run}/reports/77")

    assert response.status_code == 200
    assert "relatório do item" in response.get_data(as_text=True)


def test_a_report_route_refuses_a_run_id_the_ledger_does_not_know(client, tmp_path):
    """run_id reaches the filesystem as a path segment.

    The ledger is the allow-list: an id it never issued cannot name a directory,
    so a crafted one has nothing to reach even before the path is built.
    """
    forged = "..%2f..%2fetc"
    response = client.get(f"/api/batch/runs/{forged}/reports/1")

    assert response.status_code == 404


def test_a_missing_item_report_is_a_404_not_a_crash(client, tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")

    assert client.get(f"/api/batch/runs/{run}/reports/12345").status_code == 404


# -- the worker --------------------------------------------------------------


class FakeSession:
    """Stands in for GlpiClient / RedmineClient as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def patch_worker(monkeypatch, *, queue=(1, 2), calls=None):
    """Replace everything the batch worker reaches outside web/."""
    calls = calls if calls is not None else {}
    monkeypatch.setattr(jobs_module, "GlpiClient", lambda *a, **k: FakeSession())
    monkeypatch.setattr(jobs_module, "RedmineClient", lambda *a, **k: FakeSession())
    monkeypatch.setattr(jobs_module.cli, "run_preflight", lambda *a, **k: True)
    monkeypatch.setattr(jobs_module, "pending_roots", lambda *a, **k: list(queue))

    def fake_run_batch(glpi, redmine, mapping, ledger, run_id, issue_ids, **kwargs):
        calls["kwargs"] = kwargs
        calls["issue_ids"] = list(issue_ids)
        on_item = kwargs.get("on_item")
        for position, issue_id in enumerate(issue_ids, start=1):
            ledger.mark(run_id, issue_id, STATE_OK)
            if on_item:
                on_item(
                    position=position, total=len(issue_ids),
                    issue_id=issue_id, state=STATE_OK, detail="",
                )

    monkeypatch.setattr(jobs_module, "run_batch", fake_run_batch)
    return calls


def wait_for(job: Job, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not job.finished and time.monotonic() < deadline:
        time.sleep(0.005)


def test_a_dry_run_builds_the_queue_and_writes_nothing(client, monkeypatch):
    calls = patch_worker(monkeypatch, queue=(11, 22, 33))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "dry"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    assert job.state == "done"
    assert calls["issue_ids"] == [11, 22, 33]
    assert calls["kwargs"]["apply_mode"] is False


def test_apply_waits_on_the_typed_word_before_the_loop_runs(client, monkeypatch):
    """The queue is known BEFORE the gate, so the operator confirms a number.

    Same shape as the single-issue path, where the report is rendered before the
    confirmation and the plan that was approved is the one that gets written.
    """
    calls = patch_worker(monkeypatch, queue=(11, 22))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "apply"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])

    deadline = time.monotonic() + 5
    while job.state != "awaiting_confirm" and time.monotonic() < deadline:
        time.sleep(0.005)
    assert job.state == "awaiting_confirm"
    assert "issue_ids" not in calls, "nothing may run before the confirmation"

    client.post(f"/api/jobs/{job.id}/confirm", json={"answer": "sim"})
    wait_for(job)

    assert job.state == "done"
    assert calls["kwargs"]["apply_mode"] is True


def test_a_refused_confirmation_cancels_the_whole_batch(client, monkeypatch):
    calls = patch_worker(monkeypatch, queue=(11, 22))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "apply"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    deadline = time.monotonic() + 5
    while job.state != "awaiting_confirm" and time.monotonic() < deadline:
        time.sleep(0.005)

    client.post(f"/api/jobs/{job.id}/confirm", json={"answer": "talvez"})
    wait_for(job)

    assert job.state == "cancelled"
    assert "issue_ids" not in calls


def test_the_batch_never_passes_the_skip_flags(client, monkeypatch):
    """A closed decision: the panel migrates files and notes, always.

    The CLI keeps --skip-attachments/--skip-notes for debugging; exposing them
    on a 5451-item write run is a checkbox away from a silent partial migration.
    """
    calls = patch_worker(monkeypatch)

    body = client.post("/api/batch", json={"project": "hydro", "mode": "dry"}).get_json()
    wait_for(client.application.config["JOBS"].get(body["job_id"]))

    assert calls["kwargs"]["skip_attachments"] is False
    assert calls["kwargs"]["skip_notes"] is False


def test_progress_reaches_the_browser_as_events_not_as_printed_text(client, monkeypatch):
    patch_worker(monkeypatch, queue=(11, 22))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "dry"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    events = [event for event in job.log.follow(0) if event is not None]
    items = [event.data for event in events if event.type == "batch_item"]

    assert [item["issue_id"] for item in items] == [11, 22]
    assert [item["position"] for item in items] == [1, 2]
    assert all(item["total"] == 2 for item in items)


def test_the_summary_is_published_as_the_downloadable_report(client, monkeypatch):
    patch_worker(monkeypatch, queue=(11,))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "dry"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    assert job.report_text, "the run summary is the batch's report"
    response = client.get(f"/api/jobs/{job.id}/report")
    assert response.status_code == 200
    assert "hydro" in response.get_data(as_text=True)


def test_an_empty_queue_finishes_instead_of_asking_for_confirmation(client, monkeypatch):
    calls = patch_worker(monkeypatch, queue=())

    body = client.post("/api/batch", json={"project": "hydro", "mode": "apply"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    assert job.state == "done"
    assert "issue_ids" not in calls


def test_resume_retries_pending_and_failed_from_the_ledger(client, monkeypatch, tmp_path):
    calls = patch_worker(monkeypatch, queue=(999,))  # pending_roots must NOT be used
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1, 2, 3])
        ledger.mark(run, 1, STATE_OK)
        ledger.mark(run, 2, STATE_FAILED, "erro")

    body = client.post("/api/batch/resume", json={"run_id": run}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    # pending() puts never-tried first, then the retries.
    assert calls["issue_ids"] == [3, 2]


def test_a_second_job_is_refused_while_a_batch_runs(client, monkeypatch):
    """One slot, as before. A batch holds it for hours; that is deliberate."""
    patch_worker(monkeypatch)
    gate = threading.Event()
    original = jobs_module.run_batch
    monkeypatch.setattr(
        jobs_module,
        "run_batch",
        lambda *a, **k: (gate.wait(timeout=5), original(*a, **k)),
    )

    first = client.post("/api/batch", json={"project": "hydro", "mode": "dry"})
    assert first.status_code == 200
    second = client.post("/api/migrate", json={"issue": 20238, "mode": "dry"})

    assert second.status_code == 409
    gate.set()
    wait_for(client.application.config["JOBS"].get(first.get_json()["job_id"]))


def test_the_stream_carries_the_event_names_the_browser_listens_for(client, monkeypatch):
    """The SSE event name is the contract between web/jobs.py and app.js.

    Renaming one is invisible to every other test - the ledger, the summary and
    the report files all stay correct while the panel silently stops updating.
    """
    patch_worker(monkeypatch, queue=(11,))

    body = client.post("/api/batch", json={"project": "hydro", "mode": "dry"}).get_json()
    job = client.application.config["JOBS"].get(body["job_id"])
    wait_for(job)

    stream = client.get(f"/api/jobs/{job.id}/stream").get_data(as_text=True)
    names = {line[len("event: "):] for line in stream.splitlines() if line.startswith("event: ")}

    assert {"batch_queue", "batch_item", "batch_summary", "done", "close"} <= names


# -- a finished run must outlive the process that ran it ---------------------
#
# Reported 2026-09-08: after a page refresh the previous batch's report was
# unreachable. Nothing was lost - the files were on disk the whole time - but
# the summary was addressed by JOB id, which lives only in JobManager._jobs,
# while the durable identity of a batch is its RUN id.


def test_a_run_summary_is_served_from_disk_by_run_id(client, tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [1])
        ledger.mark(run, 1, STATE_OK)
    directory = tmp_path / "reports" / run
    directory.mkdir(parents=True)
    (directory / "resumo.txt").write_text("resumo gravado na execução", encoding="utf-8")

    response = client.get(f"/api/batch/runs/{run}/summary")

    assert response.status_code == 200
    # The file as written, not a regeneration: it is the record of what the run
    # actually reported at the time.
    assert "resumo gravado na execução" in response.get_data(as_text=True)


def test_a_missing_summary_is_regenerated_from_the_ledger(client, tmp_path):
    """The disk write can fail, and CLI runs predate the panel entirely.

    render_summary reads nothing but the ledger, so regenerating is the same
    function over the same data - not a guess at what the file would have said.
    """
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("operacao-cemig")
        ledger.queue(run, [1, 2])
        ledger.mark(run, 1, STATE_OK)
        ledger.mark(run, 2, STATE_FAILED, "anexo grande demais")

    body = client.get(f"/api/batch/runs/{run}/summary").get_data(as_text=True)

    assert "operacao-cemig" in body
    assert "anexo grande demais" in body, "a failure's reason must survive"


def test_a_summary_of_an_unknown_run_is_refused(client):
    response = client.get("/api/batch/runs/nao-existe/summary")

    assert response.status_code == 404


def test_a_finished_run_can_be_replayed_into_the_progress_table(client, tmp_path):
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("hydro")
        ledger.queue(run, [10, 20])
        ledger.mark(run, 10, STATE_OK)
        ledger.mark(run, 20, STATE_FAILED, "erro 500")

    body = client.get(f"/api/batch/runs/{run}/items").get_json()

    assert [item["issue_id"] for item in body] == [10, 20]
    assert body[1]["state"] == STATE_FAILED
    assert body[1]["detail"] == "erro 500"


def test_items_of_an_unknown_run_are_refused(client):
    assert client.get("/api/batch/runs/nao-existe/items").status_code == 404


def test_the_run_list_carries_what_the_ui_needs_to_link_to_a_run(client, tmp_path):
    """A fully successful run has resumable == 0 and used to render no actions.

    The row existed and led nowhere, which is exactly how the report went
    missing: the only durable index of past runs linked to neither the summary
    nor the item reports.
    """
    with BatchLedger(tmp_path / "b.db") as ledger:
        run = ledger.start_run("operacao-cemig")
        ledger.queue(run, [1])
        ledger.mark(run, 1, STATE_OK)

    entry = client.get("/api/batch/runs").get_json()[0]

    assert entry["resumable"] == 0
    assert entry["run_id"] == run
    # Reachable regardless: the summary route answers for every known run.
    assert client.get(f"/api/batch/runs/{run}/summary").status_code == 200

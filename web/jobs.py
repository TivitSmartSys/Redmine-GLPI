"""Background execution for the web panel.

One job at a time, in a worker thread, streaming its output to the browser.

The migration worker is a transcription of main.main(): the same five functions
in the same order inside the same open sessions. The only substitution is the
confirmation - where the CLI blocks on input(), the worker blocks on a
threading.Event released by an HTTP request. Blocking *inside* the open session
matters: it means the plan the operator approved is the exact object that gets
written, with no second fetch in between.

Live output is captured by redirecting stdout while the worker runs. The
migration functions print; nothing in the migration code path had to change to
get a live console. Only one job runs at a time, so the process-wide redirect
cannot interleave two runs.
"""

from __future__ import annotations

import contextlib
import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterator

import main as cli
from audit_coverage import collect_coverage
from clients.errors import ApiError
from clients.glpi import GlpiClient
from clients.redmine import RedmineClient
from config.settings import Settings
from report import messages
from report.reporter import Reporter
from store.db import MigrationStore
from web.summary import summarise

# How long the worker holds the GLPI session open waiting for the typed "sim".
# Long enough to read a 200-line report, short enough not to strand a session.
CONFIRM_TIMEOUT_SECONDS = 600

# Finished jobs stay addressable so a reloaded tab can still fetch the report.
MAX_RETAINED_JOBS = 20


class JobBusy(Exception):
    """Another job is still running."""


@dataclass
class Event:
    index: int
    type: str
    data: Any = None
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class _EventLog:
    """Append-only event list with blocking followers.

    A list rather than a Queue because the browser may reconnect mid-run: a
    follower replays from any cursor and then continues live.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._condition = threading.Condition()
        self._closed = False

    def append(self, type: str, data: Any = None) -> None:
        with self._condition:
            if self._closed:
                return
            event = Event(index=len(self._events), type=type, data=_scrub(data))
            self._events.append(event)
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def follow(self, cursor: int = 0, poll: float = 15.0) -> Iterator[Event | None]:
        """Yield events from `cursor` on; yield None when idle (heartbeat).

        Nothing is yielded while the lock is held - the consumer writes to a
        socket, and blocking append() for the length of that write would stall
        the worker thread.
        """
        while True:
            pending: list[Event] = []
            with self._condition:
                if cursor >= len(self._events) and not self._closed:
                    self._condition.wait(timeout=poll)
                if cursor >= len(self._events):
                    if self._closed:
                        return
                else:
                    pending = self._events[cursor:]
                    cursor = len(self._events)
            if not pending:
                yield None  # nothing new: let the caller keep the socket warm
                continue
            for event in pending:
                yield event


def _scrub(data: Any) -> Any:
    """Redaction applies to everything leaving the process (spec rule 8)."""
    if isinstance(data, str):
        return messages.redact(data)
    if isinstance(data, dict):
        return {key: _scrub(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_scrub(item) for item in data]
    return data


class _LineWriter:
    """stdout replacement that turns printed text into `log` events."""

    def __init__(self, log: _EventLog) -> None:
        self._log = log
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._log.append("log", line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._log.append("log", self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False


class Job:
    def __init__(self, kind: str, label: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind          # 'migration' | 'audit'
        self.label = label        # issue or tracker number, for the UI
        self.state = "running"    # running | awaiting_confirm | done | failed | cancelled
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.report_text: str = ""
        self.summary: dict | None = None
        self.log = _EventLog()
        self._confirm_gate = threading.Event()
        self._confirmed = False
        self.thread: threading.Thread | None = None

    # -- worker side -------------------------------------------------------

    def emit(self, type: str, data: Any = None) -> None:
        self.log.append(type, data)

    def emit_line(self, text: str) -> None:
        for line in str(text).split("\n"):
            self.log.append("log", line)

    def publish_report(self, text: str, summary: dict) -> None:
        self.report_text = messages.redact(text)
        self.summary = summary
        self.emit("report", {"text": self.report_text, "summary": summary})

    def wait_for_confirmation(self) -> bool:
        """Block inside the open session until the browser confirms."""
        self.state = "awaiting_confirm"
        self.emit("awaiting_confirm", {"timeout": CONFIRM_TIMEOUT_SECONDS})
        released = self._confirm_gate.wait(timeout=CONFIRM_TIMEOUT_SECONDS)
        if not released:
            self.emit_line(messages.UI_CONFIRM_EXPIRED)
            return False
        self.state = "running"
        return self._confirmed

    def finish(self, state: str = "done") -> None:
        self.state = state
        self.emit("done", {"state": state})
        self.log.close()

    def fail(self, detail: str) -> None:
        self.state = "failed"
        self.emit("error", messages.redact(detail))
        self.log.close()

    # -- request side ------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def confirm(self, answer: str) -> bool:
        """Server-side validation of the typed word - the browser is not trusted."""
        if self.state != "awaiting_confirm":
            return False
        accepted = str(answer or "").strip().casefold() in messages.APPLY_CONFIRM_ACCEPT
        self._confirmed = accepted
        self._confirm_gate.set()
        return accepted

    def cancel(self) -> None:
        self._confirmed = False
        self._confirm_gate.set()


class JobManager:
    """Owns the single job slot and the worker threads."""

    def __init__(self, settings: Settings, mapping: dict, db_path: str) -> None:
        self._settings = settings
        self._mapping = mapping
        self._db_path = db_path
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._current: Job | None = None

    # -- lifecycle ---------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    @property
    def busy(self) -> bool:
        return self._current is not None and not self._current.finished

    def _register(self, job: Job, target, *args) -> Job:
        with self._lock:
            if self.busy:
                raise JobBusy(messages.UI_JOB_BUSY)
            self._current = job
            self._jobs[job.id] = job
            self._prune()
        job.thread = threading.Thread(
            target=self._guard, args=(job, target, *args), daemon=True, name=f"job-{job.id}"
        )
        job.thread.start()
        return job

    def _prune(self) -> None:
        if len(self._jobs) <= MAX_RETAINED_JOBS:
            return
        for job_id in sorted(self._jobs, key=lambda key: self._jobs[key].created_at)[
            : len(self._jobs) - MAX_RETAINED_JOBS
        ]:
            if self._jobs[job_id].finished:
                del self._jobs[job_id]

    def _guard(self, job: Job, target, *args) -> None:
        """Run a worker with stdout captured and every failure reported."""
        writer = _LineWriter(job.log)
        try:
            with contextlib.redirect_stdout(writer):
                target(job, *args)
            writer.flush()
        except ApiError as exc:
            writer.flush()
            job.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the browser must see any failure
            writer.flush()
            traceback.print_exc()
            job.fail(messages.UI_UNEXPECTED_ERROR.format(detail=exc))
        finally:
            if not job.log.closed:
                job.finish()

    # -- public entry points ----------------------------------------------

    def start_migration(self, issue_id: int, apply_mode: bool) -> Job:
        job = Job(kind="migration", label=str(issue_id))
        return self._register(job, self._run_migration, issue_id, apply_mode)

    def start_audit(self, tracker: int) -> Job:
        job = Job(kind="audit", label=str(tracker))
        return self._register(job, self._run_audit, tracker)

    # -- workers -----------------------------------------------------------

    def _run_migration(self, job: Job, issue_id: int, apply_mode: bool) -> None:
        settings = self._settings
        print(messages.CLI_MODE_APPLY if apply_mode else messages.CLI_MODE_DRY_RUN)
        print()

        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi, RedmineClient(
            settings.redmine_url, settings.redmine_api_key
        ) as redmine:
            job.emit("phase", "preflight")
            if not cli.run_preflight(glpi, redmine, self._mapping, issue_id):
                job.fail(messages.PREFLIGHT_ABORTED)
                return

            # Deduplication before anything else (spec 9.1).
            existing = cli.check_already_migrated(glpi, issue_id)
            if existing:
                job.fail(
                    messages.DEDUP_ALREADY_MIGRATED.format(
                        issue_id=issue_id, glpi_id=existing
                    )
                )
                return

            job.emit("phase", "plan")
            plan = cli.build_project_plan(glpi, redmine, self._mapping, issue_id)
            job.publish_report(Reporter(plan, apply_mode=apply_mode).render(), summarise(plan))

            if not apply_mode:
                job.finish()
                return

            if not job.wait_for_confirmation():
                print(messages.APPLY_CANCELLED)
                job.finish("cancelled")
                return

            job.emit("phase", "apply")
            with MigrationStore(self._db_path) as store:
                cli.apply_plan(glpi, plan, store)

            # Re-render so the report carries the Redmine -> GLPI ids.
            job.publish_report(Reporter(plan, apply_mode=True).render(), summarise(plan))
            job.finish()

    def _run_audit(self, job: Job, tracker: int) -> None:
        result = collect_coverage(self._settings, self._mapping, tracker, emit=print)
        job.emit("audit_result", asdict(result))
        job.finish()

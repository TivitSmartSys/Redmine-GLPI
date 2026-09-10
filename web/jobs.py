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

import bisect
import contextlib
import sqlite3
import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import main as cli
import migration_status as status_tool
from audit_coverage import collect_coverage
from batch.report import render_summary
from batch.runner import run_batch
from batch.selection import REDMINE_PROJECTS, pending_roots
from clients.errors import ApiError
from clients.glpi import GlpiClient
from clients.redmine import RedmineClient
from config.settings import Settings
from report import messages
from report.reporter import Reporter
from store.batch import BatchLedger
from store.db import MigrationStore
from web.summary import summarise

# How long the worker holds the GLPI session open waiting for the typed "sim".
# Long enough to read a 200-line report, short enough not to strand a session.
CONFIRM_TIMEOUT_SECONDS = 600

# Finished jobs stay addressable so a reloaded tab can still fetch the report.
MAX_RETAINED_JOBS = 20

# How many `log` events a BATCH job keeps in memory.
#
# The single-issue path is deliberately unbounded and stays that way: one issue
# prints a few hundred lines and the operator reads all of them. A batch does
# not fit that shape. main.py prints from 65 sites and Telecom queues 5451
# roots, so an unbounded log pins hundreds of thousands of events in the process
# for the whole run - and replays every one of them into the DOM when a tab
# reconnects. Only `log` events are dropped; the events the UI reasons about
# (batch_queue, batch_item, batch_summary, done, error) are never trimmed, so
# the progress table and the arithmetic survive intact however long the run is.
#
# The per-item reports on disk remain the complete record. This cap governs the
# live console only.
MAX_BATCH_LOG_EVENTS = 4000

# How long the batch worker waits for the typed "sim" before giving the session
# up. Longer than a single issue's gate: the operator is being asked to approve
# thousands of writes and will want to read the queue count and the run id.
BATCH_CONFIRM_TIMEOUT_SECONDS = 900

# Lines held before the transcript file can be opened. The run id - and so the
# directory - exists only after the queue is built, and preflight prints well
# under this. A cap rather than an unbounded list, because an aborted preflight
# never attaches a sink at all.
MAX_PENDING_LOG_LINES = 2000

# The batch transcript, beside the per-item reports it explains.
CONSOLE_FILENAME = "console.txt"


class JobBusy(Exception):
    """Another job is still running."""


class ReportsDirUnwritable(Exception):
    """Every artefact of a run lands in one directory, and it is not writable.

    Diagnosed 2026-09-10 in production: the app lives in a directory systemd
    mounts read-only, and the reports directory defaulted to a path relative to
    it, so `mkdir` answered EROFS. It surfaced as a bare "Erro inesperado:
    [Errno 30]" after ~100 s already spent reading Redmine, which named neither
    the path nor the way out.
    """


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

    `max_log_events` caps how many `log` events are retained; None keeps every
    one, which is what the single-issue and audit paths use. Only `log` events
    are ever dropped - see MAX_BATCH_LOG_EVENTS.

    Once trimming starts, `Event.index` is no longer the event's position in
    the list: indices stay monotonic but develop holes. Every reader therefore
    goes through bisect on the index rather than slicing by position, and a
    follower arriving with a cursor that points into a hole simply gets the
    oldest event that is still retained.
    """

    def __init__(self, max_log_events: int | None = None) -> None:
        self._events: list[Event] = []
        self._condition = threading.Condition()
        self._closed = False
        self._max_log_events = max_log_events
        self._log_count = 0
        self._next_index = 0
        self.dropped_logs = 0

    def append(self, type: str, data: Any = None) -> None:
        with self._condition:
            if self._closed:
                return
            event = Event(index=self._next_index, type=type, data=_scrub(data))
            self._next_index += 1
            self._events.append(event)
            if type == "log":
                self._log_count += 1
                self._trim()
            self._condition.notify_all()

    def _trim(self) -> None:
        """Drop the oldest `log` events once the cap is exceeded.

        Called with the lock held. Trimming happens in chunks rather than one
        event at a time: a rebuild is O(n) and a batch appends hundreds of
        thousands of lines, so trimming on every append past the cap would make
        the log quadratic in the size of the run.
        """
        if self._max_log_events is None or self._log_count <= self._max_log_events:
            return
        chunk = max(1, self._max_log_events // 4)
        excess = self._log_count - self._max_log_events + chunk
        kept: list[Event] = []
        dropped = 0
        for event in self._events:
            if dropped < excess and event.type == "log":
                dropped += 1
                continue
            kept.append(event)
        self._events = kept
        self._log_count -= dropped
        self.dropped_logs += dropped

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    def _position(self, cursor: int) -> int:
        """First retained event with index >= cursor. Lock must be held."""
        return bisect.bisect_left(self._events, cursor, key=lambda event: event.index)

    def follow(self, cursor: int = 0, poll: float = 15.0) -> Iterator[Event | None]:
        """Yield events from `cursor` on; yield None when idle (heartbeat).

        Nothing is yielded while the lock is held - the consumer writes to a
        socket, and blocking append() for the length of that write would stall
        the worker thread.
        """
        while True:
            pending: list[Event] = []
            with self._condition:
                if self._position(cursor) >= len(self._events) and not self._closed:
                    self._condition.wait(timeout=poll)
                position = self._position(cursor)
                if position >= len(self._events):
                    if self._closed:
                        return
                else:
                    pending = self._events[position:]
                    # Advance by what was RECEIVED, not by the list length: the
                    # two differ the moment trimming has opened a hole.
                    cursor = pending[-1].index + 1
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
    """stdout replacement that turns printed text into `log` events.

    For a batch it also tees every line to `reports/<run-id>/console.txt`. That
    transcript is the run's operational narrative - the order items were taken
    in, the GLPI id each root became, every warning printed while applying -
    and until 2026-09-08 it existed nowhere but this process's memory, so a
    page refresh or a restart lost it. The CLI never persisted it either; the
    August 2026 runs were captured by hand with a shell redirect.

    Two consequences worth keeping:
      * the file is line-buffered, so a run that dies halfway still leaves
        everything printed up to that point - which is when a transcript is
        worth most;
      * the file is complete even though the in-memory log is capped, which is
        what makes MAX_BATCH_LOG_EVENTS an honest trade rather than data loss.

    The run id only exists after the queue is built, so the sink is attached
    mid-run and the lines printed before it (preflight) wait in `_pending`.
    That buffer is bounded and only fills for a job that intends to attach one:
    a single-issue job never does, and letting it accumulate would reintroduce
    the very leak the cap exists to prevent.
    """

    def __init__(self, log: _EventLog, buffer_pending: bool = False) -> None:
        self._log = log
        self._buffer = ""
        self._sink = None
        self._pending: list[str] | None = [] if buffer_pending else None
        self._sink_broken = False

    # -- the transcript file ----------------------------------------------

    def _open_sink(self, path: Path):
        """Isolated so a test can refuse it; see the OSError handling below."""
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("a", encoding="utf-8", errors="replace", buffering=1)

    def attach_file(self, path: Path) -> None:
        try:
            self._sink = self._open_sink(path)
            for line in self._pending or ():
                self._sink.write(line + "\n")
        except OSError as exc:
            # Never fatal: the transcript is bookkeeping, the migration is the
            # work. Reported through the log rather than print(), which would
            # re-enter this writer.
            self._sink = None
            self._sink_broken = True
            self._log.append(
                "log",
                messages.BATCH_CONSOLE_WRITE_FAILED.format(
                    path=path, detail=messages.redact(exc)
                ),
            )
        finally:
            self._pending = None

    def close_file(self) -> None:
        if self._sink is not None:
            try:
                self._sink.close()
            except OSError:
                pass
            self._sink = None

    # -- stdout ------------------------------------------------------------

    def _emit(self, line: str) -> None:
        # Redacted once, here: the file leaves the process exactly as the event
        # stream does, and rule 5 covers both.
        line = messages.redact(line)
        self._log.append("log", line)
        if self._sink is not None:
            try:
                self._sink.write(line + "\n")
            except OSError as exc:
                self._sink = None
                self._sink_broken = True
                self._log.append(
                    "log",
                    messages.BATCH_CONSOLE_WRITE_FAILED.format(
                        path=CONSOLE_FILENAME, detail=messages.redact(exc)
                    ),
                )
        elif self._pending is not None and len(self._pending) < MAX_PENDING_LOG_LINES:
            self._pending.append(line)

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._emit(self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False


class Job:
    def __init__(
        self,
        kind: str,
        label: str,
        *,
        max_log_events: int | None = None,
        confirm_timeout: float | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind          # 'migration' | 'audit' | 'batch'
        self.label = label        # issue, tracker or project name, for the UI
        self.state = "running"    # running | awaiting_confirm | done | failed | cancelled
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.report_text: str = ""
        self.summary: dict | None = None
        self.run_id: str = ""     # batch only: the ledger run this job drives
        self.writer = None        # set by JobManager._guard; tees the console
        self.log = _EventLog(max_log_events=max_log_events)
        # Read from the module at construction time, NOT as a default argument:
        # a default is bound once when the class is defined, which would freeze
        # the constant at import and silently ignore any later change to it.
        self._confirm_timeout = (
            CONFIRM_TIMEOUT_SECONDS if confirm_timeout is None else confirm_timeout
        )
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

    def publish_batch_summary(self, text: str, counts: dict) -> None:
        """The batch's equivalent of publish_report.

        A separate event because the payloads are not interchangeable: the
        single-issue `report` carries web.summary.summarise(plan), which is a
        statement about FIELDS of one project, while this one carries the
        ledger's state counts, a statement about ITEMS. Feeding one to the
        other's renderer would produce a plausible-looking wrong number.
        """
        self.report_text = messages.redact(text)
        self.summary = {"counts": counts, "run_id": self.run_id, "label": self.label}
        self.emit("batch_summary", {"text": self.report_text, **self.summary})

    def wait_for_confirmation(self, detail: dict | None = None) -> bool:
        """Block inside the open session until the browser confirms."""
        self.state = "awaiting_confirm"
        self.emit(
            "awaiting_confirm", {"timeout": self._confirm_timeout, **(detail or {})}
        )
        released = self._confirm_gate.wait(timeout=self._confirm_timeout)
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

    def __init__(
        self,
        settings: Settings,
        mapping: dict,
        db_path: str,
        reports_dir: str = "reports",
    ) -> None:
        self._settings = settings
        self._mapping = mapping
        self._db_path = db_path
        self._reports_dir = Path(reports_dir)
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
        # Only a batch buffers ahead of a sink, and only a batch gets one: it is
        # the one job kind whose transcript has to outlive the process.
        writer = _LineWriter(job.log, buffer_pending=job.kind == "batch")
        job.writer = writer
        try:
            with contextlib.redirect_stdout(writer):
                target(job, *args)
            writer.flush()
        except (ApiError, ReportsDirUnwritable) as exc:
            # Both already say what happened in PT-BR and what to do about it;
            # wrapping either in "Erro inesperado" would bury the instruction.
            writer.flush()
            job.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - the browser must see any failure
            writer.flush()
            traceback.print_exc()
            job.fail(messages.UI_UNEXPECTED_ERROR.format(detail=exc))
        finally:
            # Closed after the last flush and after job.finish() has had its
            # chance to print, so the transcript ends where the run ends.
            if not job.log.closed:
                job.finish()
            writer.close_file()

    # -- public entry points ----------------------------------------------

    def start_migration(self, issue_id: int, apply_mode: bool) -> Job:
        job = Job(kind="migration", label=str(issue_id))
        return self._register(job, self._run_migration, issue_id, apply_mode)

    def start_audit(self, tracker: int) -> Job:
        job = Job(kind="audit", label=str(tracker))
        return self._register(job, self._run_audit, tracker)

    def start_batch(
        self, project: str, apply_mode: bool, limit: int | None = None
    ) -> Job:
        job = Job(
            kind="batch",
            label=project,
            max_log_events=MAX_BATCH_LOG_EVENTS,
            confirm_timeout=BATCH_CONFIRM_TIMEOUT_SECONDS,
        )
        return self._register(job, self._run_batch, project, apply_mode, limit, None)

    def start_status(self, verify: bool = False) -> Job:
        """Re-read the instance for the progress tab. No write mode, by design.

        It gets the batch's log ceiling because it prints one warning per
        unreadable project and the instance holds a thousand of them.
        """
        job = Job(
            kind="status",
            label=messages.UI_NAV_STATUS,
            max_log_events=MAX_BATCH_LOG_EVENTS,
        )
        return self._register(job, self._run_status, verify)

    def resume_batch(self, run_id: str, apply_mode: bool) -> Job:
        """Retry whatever the ledger still shows as pending or failed.

        The project comes from the ledger, not from the caller: a run belongs
        to the project it was started for, and letting a request name a
        different one would queue Telecom's roots under HYDRO's run id.
        """
        with BatchLedger(self._db_path) as ledger:
            label = ledger.run_label(run_id)
        job = Job(
            kind="batch",
            label=str(label or run_id),
            max_log_events=MAX_BATCH_LOG_EVENTS,
            confirm_timeout=BATCH_CONFIRM_TIMEOUT_SECONDS,
        )
        return self._register(job, self._run_batch, label, apply_mode, None, run_id)

    def run_exists(self, run_id: str) -> bool:
        with BatchLedger(self._db_path) as ledger:
            return ledger.run_exists(run_id)

    def runs(self) -> list[dict]:
        with BatchLedger(self._db_path) as ledger:
            return ledger.runs()

    def ensure_reports_dir(self) -> Path:
        """The directory a run writes into, proven writable *now*.

        Proven, not assumed: an existing directory can still refuse a write,
        and the failure we are guarding against costs minutes of reading before
        it surfaces. The probe is a real file because that is the only thing
        that answers the real question - `os.access` lies on a read-only mount
        and on anything with ACLs.
        """
        path = self._reports_dir
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".escrita-teste"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise ReportsDirUnwritable(
                messages.UI_REPORTS_DIR_UNWRITABLE.format(
                    path=path, detail=messages.redact(exc)
                )
            ) from exc
        return path

    def report_path(self, run_id: str, issue_id: int) -> Path:
        return self._reports_dir / run_id / f"RDM{int(issue_id)}.txt"

    def items(self, run_id: str) -> list[dict]:
        with BatchLedger(self._db_path) as ledger:
            return ledger.items(run_id)

    def run_console(self, run_id: str) -> str | None:
        """A run's printed transcript, or None when it has none.

        None rather than a reconstruction: the transcript's whole value is
        being what was actually printed, so a run that predates this feature
        must say it has nothing rather than offer a plausible substitute.
        """
        path = self._reports_dir / run_id / CONSOLE_FILENAME
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def run_summary(self, run_id: str) -> str:
        """A finished run's summary, addressed by RUN id rather than job id.

        Reported 2026-09-08: after a page refresh the previous batch's report
        was unreachable. The files were on disk the whole time - the summary was
        simply addressed by job id, which lives only in JobManager._jobs and is
        pruned at MAX_RETAINED_JOBS, so it could not outlive the process.

        The file on disk wins when it exists: it is the record the run actually
        wrote. Regeneration is the fallback, and it is exact rather than
        approximate - render_summary reads nothing but the ledger, which is the
        same source the original write used. That also makes this answer for
        runs driven from the CLI before the panel existed, and for a run whose
        disk write failed.
        """
        path = self._reports_dir / run_id / "resumo.txt"
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            pass
        with BatchLedger(self._db_path) as ledger:
            return render_summary(ledger, run_id, ledger.run_label(run_id) or run_id)

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
                # `redmine` is what enables step 5: the attachment bytes can
                # only come from the source system. Step 6 (notes) needs no
                # client - the journal text already came down with the tree.
                # The panel exposes no skip flags, so both steps always run.
                cli.apply_plan(glpi, plan, store, redmine=redmine)

            # Re-render so the report carries the Redmine -> GLPI ids.
            job.publish_report(Reporter(plan, apply_mode=True).render(), summarise(plan))
            job.finish()

    def _run_batch(
        self,
        job: Job,
        project: str,
        apply_mode: bool,
        limit: int | None,
        resume_run_id: str | None,
    ) -> None:
        """A transcription of migrate_batch.main(), as _run_migration is of main().

        Same order, same single preflight, same ledger, same confirmation gate.
        The only substitutions are the ones the panel forces: the gate is an
        HTTP request instead of input(), and run_batch gets an `on_item`
        callback so progress reaches the browser as events rather than as text
        scraped out of stdout.

        Deliberately NOT passed: skip_attachments and skip_notes. The panel
        migrates files and notes, always - the same closed decision the
        single-issue worker records.
        """
        settings = self._settings
        print(messages.CLI_MODE_APPLY if apply_mode else messages.CLI_MODE_DRY_RUN)
        print()

        # The same guard as the status job, for the same reason and one step
        # earlier: a batch reaches its first mkdir only after preflight and
        # after building the pending list - a bulk read of every container row
        # plus a full Redmine sweep. `batch/runner.py` would then die on EROFS
        # with nothing migrated and nothing explained.
        self.ensure_reports_dir()

        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi, RedmineClient(
            settings.redmine_url, settings.redmine_api_key
        ) as redmine:
            job.emit("phase", "preflight")
            # ONCE for the whole batch, not once per item: it opens the session,
            # widens the entity tree and loads every dropdown dictionary.
            # issue_id=None because there is no single root whose tracker could
            # be checked here - each item is checked by build_project_plan.
            if not cli.run_preflight(glpi, redmine, self._mapping, issue_id=None):
                job.fail(messages.PREFLIGHT_ABORTED)
                return

            with BatchLedger(self._db_path) as ledger:
                job.emit("phase", "queue")
                if resume_run_id:
                    run_id = resume_run_id
                    queue = ledger.pending(run_id)
                else:
                    run_id = ledger.start_run(project)
                    queue = pending_roots(
                        glpi, redmine, REDMINE_PROJECTS[project],
                        limit=limit, project_id=project,
                    )
                    ledger.queue(run_id, queue)

                job.run_id = run_id
                # The earliest moment the transcript can be named. Everything
                # printed before this - the mode line, the whole preflight -
                # has been waiting in the writer's pending buffer.
                report_dir = self._reports_dir / run_id
                if job.writer is not None:
                    job.writer.attach_file(report_dir / CONSOLE_FILENAME)

                print(messages.BATCH_QUEUE.format(count=len(queue), run_id=run_id))
                job.emit(
                    "batch_queue",
                    {
                        "run_id": run_id,
                        "count": len(queue),
                        "project": job.label,
                        "apply": apply_mode,
                        "resumed": bool(resume_run_id),
                    },
                )

                if not queue:
                    # Nothing to approve. Asking for a confirmation here would
                    # train the operator to type "sim" at an empty queue.
                    print(messages.BATCH_NOTHING_TO_DO)
                    job.publish_batch_summary(
                        render_summary(ledger, run_id, job.label), ledger.counts(run_id)
                    )
                    job.finish()
                    return

                if apply_mode and not job.wait_for_confirmation({"count": len(queue)}):
                    print(messages.APPLY_CANCELLED)
                    job.finish("cancelled")
                    return

                job.emit("phase", "apply" if apply_mode else "plan")
                run_batch(
                    glpi, redmine, self._mapping, ledger, run_id, queue,
                    apply_mode=apply_mode,
                    report_dir=report_dir,
                    db_path=self._db_path,
                    skip_attachments=False,
                    skip_notes=False,
                    on_item=lambda **event: job.emit("batch_item", event),
                )

                summary = render_summary(ledger, run_id, job.label)
                # The same resumo.txt migrate_batch.py writes, in the same place,
                # so a run driven from the panel leaves the same record on disk
                # as one driven from the CLI.
                try:
                    report_dir.mkdir(parents=True, exist_ok=True)
                    (report_dir / "resumo.txt").write_text(summary, encoding="utf-8")
                except OSError as exc:
                    # The writes already happened; a disk problem must not turn
                    # a finished run into a failed one.
                    print(
                        messages.BATCH_SUMMARY_WRITE_FAILED.format(
                            path=report_dir / "resumo.txt", detail=messages.redact(exc)
                        )
                    )
                job.publish_batch_summary(summary, ledger.counts(run_id))
                job.finish()

    def _run_audit(self, job: Job, tracker: int) -> None:
        result = collect_coverage(self._settings, self._mapping, tracker, emit=print)
        job.emit("audit_result", asdict(result))
        job.finish()

    def _run_status(self, job: Job, verify: bool) -> None:
        """A transcription of migration_status.main(), like the two above.

        There is no apply_mode here and there must never be one: every call in
        this path is a GET, on both sides. The job exists only because the read
        is slow - 684 projects took ~45 minutes on 2026-08-31 - so it needs the
        same console, the same one-job-at-a-time gate and the same live stream
        the other workers have.

        The scan writes exactly one thing, and it is local: the cache the tab
        renders from, plus the same status.html the CLI produces.
        """
        settings = self._settings
        print(messages.UI_STATUS_READONLY)
        print()

        # First, and before a single GET. The read that follows costs minutes;
        # discovering at the end of it that the result cannot be saved wastes
        # all of it and leaves the tab exactly as empty as before.
        reports_dir = self.ensure_reports_dir()

        conn = sqlite3.connect(self._db_path)
        try:
            with GlpiClient(
                settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
            ) as glpi, RedmineClient(
                settings.redmine_url, settings.redmine_api_key
            ) as redmine:
                rows = status_tool.collect(glpi, conn, redmine if verify else None)
                print(messages.UI_STATUS_MEASURING)
                scope = status_tool.measure_scope(redmine)
                imported = status_tool.measure_imported(glpi, rows)
        finally:
            conn.close()

        status_tool.save_cache(
            rows, scope, imported, path=reports_dir / status_tool.CACHE_FILENAME
        )
        snapshot = status_tool.Snapshot(
            rows=rows,
            saved_at=f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            scope=scope,
            imported=imported,
        )
        # The same page the CLI writes, in the same place. The tab renders from
        # the cache, so this file is for sharing and for the CLI's own users -
        # not the tab's source.
        try:
            page = status_tool.render_html(rows, snapshot.totals())
            (reports_dir / "status.html").write_text(page, encoding="utf-8")
        except OSError as exc:
            # The reading is done and cached; a disk problem must not turn a
            # finished scan into a failed one.
            print(messages.redact(exc))

        job.emit("status_done", {"projects": len(rows), "saved_at": snapshot.saved_at})
        job.finish()

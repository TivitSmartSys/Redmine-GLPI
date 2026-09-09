"""Flask routes for the migration panel.

Binds to localhost, serves one page and a small JSON/SSE API. There is no auth:
this is a single-operator tool running on the operator's own machine, and it
holds no secrets the machine does not already have in .env.

Two rules govern every response:
  * nothing leaves the process without messages.redact();
  * .env values are never sent - only whether each key is defined.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from batch.selection import REDMINE_PROJECTS
from clients.errors import ApiError
from clients.glpi import GlpiClient
from clients.redmine import RedmineClient
from config import settings as config
from config.settings import ConfigError, load_settings, load_yaml
from report import messages
from report.reporter import default_report_path
from store.db import MigrationStore
from web.jobs import JobBusy, JobManager

UI_STRINGS = {
    name: getattr(messages, name) for name in dir(messages) if name.startswith("UI_")
}


def create_app(db_path: str | None = None, reports_dir: str | None = None) -> Flask:
    """Build the app. Raises ConfigError when .env is incomplete."""
    settings = load_settings()
    messages.register_secrets(settings.secret_values())
    mapping = load_yaml("mapping.yml")

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["MAPPING"] = mapping
    app.config["DB_PATH"] = db_path or str(config.DEFAULT_DB_PATH)
    # Same default as migrate_batch.py, so a run driven from the panel leaves
    # its per-item reports exactly where a CLI run leaves them.
    app.config["REPORTS_DIR"] = reports_dir or "reports"
    app.config["JOBS"] = JobManager(
        settings, mapping, app.config["DB_PATH"], app.config["REPORTS_DIR"]
    )
    # SSE responses must not be buffered or the console stops being live.
    app.config["JSON_SORT_KEYS"] = False

    _register_routes(app)
    return app


def _jobs(app: Flask | None = None) -> JobManager:
    from flask import current_app

    return (app or current_app).config["JOBS"]


def _error(detail: str, status: int = 400) -> Response:
    response = jsonify({"error": messages.redact(detail)})
    response.status_code = status
    return response


def _register_routes(app: Flask) -> None:

    # -- page ------------------------------------------------------------

    @app.get("/")
    def index():
        # The project list is static configuration, so it is rendered into the
        # page rather than fetched: one fewer request, and the selector cannot
        # come up empty because a call failed.
        return render_template(
            "index.html", ui=UI_STRINGS, batch_projects=_batch_projects()
        )

    # -- connection health -----------------------------------------------

    @app.get("/api/health")
    def health():
        settings = app.config["SETTINGS"]
        result = {}
        try:
            with GlpiClient(
                settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
            ):
                pass
            result["glpi"] = {"ok": True}
        except ApiError as exc:
            result["glpi"] = {"ok": False, "detail": messages.redact(exc)}

        try:
            with RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
                # RedmineClient opens no connection on __enter__, so the check
                # has to be a real authenticated request. One issue is enough.
                tracker = sorted(config.IN_SCOPE_ROOT_TRACKERS)[0]
                next(redmine.iter_issues(tracker, page_size=1), None)
            result["redmine"] = {"ok": True}
        except ApiError as exc:
            result["redmine"] = {"ok": False, "detail": messages.redact(exc)}
        return jsonify(result)

    # -- jobs -------------------------------------------------------------

    @app.post("/api/migrate")
    def migrate():
        body = request.get_json(silent=True) or {}
        try:
            issue_id = int(body.get("issue"))
        except (TypeError, ValueError):
            return _error(messages.UI_ISSUE_INVALID)
        if issue_id <= 0:
            return _error(messages.UI_ISSUE_INVALID)

        apply_mode = body.get("mode") == "apply"
        try:
            job = _jobs(app).start_migration(issue_id, apply_mode)
        except JobBusy as exc:
            return _error(str(exc), status=409)
        return jsonify({"job_id": job.id, "mode": "apply" if apply_mode else "dry"})

    @app.post("/api/audit")
    def audit():
        body = request.get_json(silent=True) or {}
        try:
            tracker = int(body.get("tracker"))
        except (TypeError, ValueError):
            return _error(messages.UI_TRACKER_INVALID)
        try:
            job = _jobs(app).start_audit(tracker)
        except JobBusy as exc:
            return _error(str(exc), status=409)
        return jsonify({"job_id": job.id})

    # -- batch (fase 1) ---------------------------------------------------

    @app.post("/api/batch")
    def batch():
        body = request.get_json(silent=True) or {}
        project = str(body.get("project") or "")
        if project not in REDMINE_PROJECTS:
            return _error(messages.UI_BATCH_PROJECT_INVALID)

        limit = body.get("limit")
        if limit in (None, "", "todos"):
            limit = None
        else:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                return _error(messages.UI_BATCH_LIMIT_INVALID)
            # 0 is rejected rather than treated as "all": pending_roots reads
            # limit=0 as "none", so accepting it here would silently start a
            # run that does nothing.
            if limit <= 0:
                return _error(messages.UI_BATCH_LIMIT_INVALID)

        apply_mode = body.get("mode") == "apply"
        try:
            job = _jobs(app).start_batch(project, apply_mode, limit)
        except JobBusy as exc:
            return _error(str(exc), status=409)
        return jsonify({"job_id": job.id, "mode": "apply" if apply_mode else "dry"})

    @app.post("/api/batch/resume")
    def batch_resume():
        body = request.get_json(silent=True) or {}
        run_id = str(body.get("run_id") or "")
        # An unknown id yields an empty queue, which is indistinguishable from
        # a finished run - the trap migrate_batch.py hit on 2026-08-27. Say so
        # instead of starting a job that reports "nothing pending".
        if not _jobs(app).run_exists(run_id):
            return _error(
                messages.UI_BATCH_RUN_NOT_FOUND.format(run_id=run_id), status=404
            )

        apply_mode = body.get("mode") == "apply"
        try:
            job = _jobs(app).resume_batch(run_id, apply_mode)
        except JobBusy as exc:
            return _error(str(exc), status=409)
        return jsonify({"job_id": job.id, "mode": "apply" if apply_mode else "dry"})

    @app.get("/api/batch/runs")
    def batch_runs():
        return jsonify(_jobs(app).runs())

    @app.get("/api/batch/runs/<run_id>/summary")
    def batch_run_summary(run_id: str):
        """A past run's resumo.txt, reachable after any refresh or restart.

        Keyed on the run id, which is durable in both places that matter - the
        ledger and reports/<run-id>/ - unlike the job id, which dies with the
        process.
        """
        jobs = _jobs(app)
        if not jobs.run_exists(run_id):
            return _error(
                messages.UI_BATCH_RUN_NOT_FOUND.format(run_id=run_id), status=404
            )
        return Response(
            messages.redact(jobs.run_summary(run_id)),
            mimetype="text/plain",
            headers={"Content-Disposition": f'inline; filename="resumo_{run_id}.txt"'},
        )

    @app.get("/api/batch/runs/<run_id>/items")
    def batch_run_items(run_id: str):
        """The rows that rebuild the progress table for a finished run."""
        jobs = _jobs(app)
        if not jobs.run_exists(run_id):
            return _error(
                messages.UI_BATCH_RUN_NOT_FOUND.format(run_id=run_id), status=404
            )
        return jsonify(jobs.items(run_id))

    @app.get("/api/batch/runs/<run_id>/reports/<int:issue_id>")
    def batch_item_report(run_id: str, issue_id: int):
        """One item's report, straight off disk.

        `run_id` reaches the filesystem as a path segment, so the ledger is the
        allow-list: an id it never issued names no directory. `issue_id` is an
        int by the URL converter, so neither half of the path can be crafted.
        """
        jobs = _jobs(app)
        if not jobs.run_exists(run_id):
            return _error(
                messages.UI_BATCH_RUN_NOT_FOUND.format(run_id=run_id), status=404
            )
        path = jobs.report_path(run_id, issue_id)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)
        return Response(
            messages.redact(text),
            mimetype="text/plain",
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    @app.get("/api/jobs/<job_id>/stream")
    def stream(job_id: str):
        job = _jobs(app).get(job_id)
        if job is None:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)

        cursor = request.args.get("from", default=0, type=int)

        def generate():
            # Replays from `cursor`, so a reloaded tab rejoins a running job.
            for event in job.log.follow(cursor):
                if event is None:
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps(
                    {"index": event.index, "type": event.type, "data": event.data},
                    ensure_ascii=False,
                )
                yield f"event: {event.type}\ndata: {payload}\n\n"
            yield "event: close\ndata: {}\n\n"

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/jobs/<job_id>")
    def job_state(job_id: str):
        job = _jobs(app).get(job_id)
        if job is None:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)
        return jsonify(
            {
                "id": job.id,
                "kind": job.kind,
                "label": job.label,
                "state": job.state,
                "summary": job.summary,
                "has_report": bool(job.report_text),
            }
        )

    @app.post("/api/jobs/<job_id>/confirm")
    def confirm(job_id: str):
        job = _jobs(app).get(job_id)
        if job is None:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)
        body = request.get_json(silent=True) or {}
        # The typed word is validated here, not in the browser.
        if not job.confirm(body.get("answer", "")):
            return _error(messages.UI_CONFIRM_REJECTED, status=400)
        return jsonify({"confirmed": True})

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel(job_id: str):
        job = _jobs(app).get(job_id)
        if job is None:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)
        job.cancel()
        return jsonify({"cancelled": True})

    @app.get("/api/jobs/<job_id>/report")
    def report(job_id: str):
        job = _jobs(app).get(job_id)
        if job is None or not job.report_text:
            return _error(messages.UI_JOB_NOT_FOUND, status=404)
        # A batch's report is the run summary, not a per-issue report, and it
        # is the same text migrate_batch.py saves as resumo.txt.
        filename = (
            f"resumo_{job.run_id or job.label}.txt"
            if job.kind == "batch"
            else default_report_path(job.label).name
        )
        return Response(
            job.report_text,
            mimetype="text/plain",  # Flask appends charset=utf-8 itself
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # -- history ----------------------------------------------------------

    @app.get("/api/history")
    def history():
        with MigrationStore(app.config["DB_PATH"]) as store:
            entries = store.all_entries()
        return jsonify([asdict(entry) for entry in entries])

    # -- configuration (read-only) ----------------------------------------

    @app.get("/api/config")
    def configuration():
        mapping = app.config["MAPPING"]
        settings = app.config["SETTINGS"]
        return jsonify(
            {
                # Which instance this panel is pointed at. Hosts only: after the
                # move from TEST to PRODUCTION on 2026-09-03 the same panel can
                # be aimed at either, and a container id or an entity id read
                # here means nothing until you know which server answered.
                # Settings.secret_values() covers the three tokens; a hostname
                # is not one of them.
                "instance": {
                    "glpi": _host(settings.glpi_url),
                    "redmine": _host(settings.redmine_url),
                },
                # Presence only. Values never leave the server.
                "env": [
                    {"name": name, "present": bool((os.environ.get(name) or "").strip())}
                    for name in config.REQUIRED_ENV_VARS
                ],
                "scope": {
                    "container_additional_fields": config.CONTAINER_ID_ADDITIONAL_FIELDS,
                    "itemtype_additional_fields": config.ITEMTYPE_ADDITIONAL_FIELDS,
                    "container_faturamento": config.CONTAINER_ID_FATURAMENTO,
                    "itemtype_faturamento": config.ITEMTYPE_FATURAMENTO,
                    "tracker_faturamento": config.TRACKER_FATURAMENTO,
                    "task_trackers": sorted(config.IN_SCOPE_TASK_TRACKERS),
                    "root_trackers": sorted(config.IN_SCOPE_ROOT_TRACKERS),
                    "tracker_atividades": config.TRACKER_ATIVIDADES,
                    "projecttasktypes": config.TRACKER_TO_PROJECTTASKTYPE,
                    "mandatory_columns": list(config.MANDATORY_CONTAINER15_COLUMNS),
                    "mandatory_columns_container26": list(
                        config.MANDATORY_CONTAINER26_COLUMNS
                    ),
                    "db_path": str(app.config["DB_PATH"]),
                    "reports_dir": str(app.config["REPORTS_DIR"]),
                },
                # The numbers that decide where data lands and what gets cut.
                # Every one of them has moved at least once (the entity default
                # in 2026-08-12, the UTC offset in 2026-08-27, the document
                # ceiling from 50 to 10 MB when .env moved to production), and
                # none of them was visible anywhere in the panel.
                "constants": {
                    "default_entity_id": config.DEFAULT_ENTITY_ID,
                    "utc_offset_hours": config.REDMINE_TO_GLPI_UTC_OFFSET_HOURS,
                    "document_max_size_mb": config.DOCUMENT_MAX_SIZE_MB,
                    "plugin_text_max_length": config.PLUGIN_TEXT_MAX_LENGTH,
                    "container_task_additional_fields": (
                        config.CONTAINER_ID_TASK_ADDITIONAL_FIELDS
                    ),
                },
                "batch_projects": _batch_projects(),
                "entity_map": _entity_rows(),
                "mapping": {
                    section: _mapping_rows(mapping.get(section) or [])
                    for section in (
                        "project_core",
                        "container15",
                        "container26",
                        "task_core",
                    )
                },
                "never_write": [
                    {"column": item.get("column"), "reason": item.get("reason", "")}
                    for item in (mapping.get("never_write") or [])
                ],
                "status_map": _safe_yaml("status_map.yml"),
                "user_map": _safe_yaml("user_map.yml"),
            }
        )


def _host(url: str) -> str:
    """The host of a configured URL, for display. Never the credentials."""
    parts = urlsplit(url if "//" in url else f"//{url}")
    return parts.hostname or url


def _batch_projects() -> list[dict]:
    """The Redmine projects the batch view can run, with their root tracker."""
    return [
        {"id": name, "tracker": tracker}
        for name, tracker in sorted(REDMINE_PROJECTS.items())
    ]


def _entity_rows() -> dict:
    """Cliente -> GLPI entity, from entity_map.yml, one row per client name.

    Shown because it decides the entity of every project written, and because a
    Cliente missing from it is NOT an error - the project quietly goes to
    DEFAULT_ENTITY_ID instead. Reading the file is the only way to see that
    coming; 1060 of 5594 roots (19%) landed there when the map was measured.

    The file's own shape is entity-first (one entity, many clients); the panel
    is read client-first, because the question an operator asks is "where does
    THIS cliente go". `nao_sera_migrado` is returned separately and NOT folded
    in: those names are in the sheet deliberately, and showing them as merely
    absent would lose the distinction between "withheld" and "not yet mapped".
    """
    data = _safe_yaml(config.ENTITY_MAP_FILENAME)
    if not isinstance(data, dict):
        return {"clients": [], "nao_sera_migrado": []}

    rows = []
    for entry in data.get("entities") or []:
        completename = str(entry.get("completename") or "").strip()
        for client in entry.get("clients") or []:
            rows.append(
                {
                    "client": str(client),
                    "entity": completename,
                    "id_teste": entry.get("id_teste"),
                }
            )
    rows.sort(key=lambda row: row["client"].casefold())
    return {
        "clients": rows,
        "nao_sera_migrado": [str(name) for name in data.get("nao_sera_migrado") or []],
    }


def _mapping_rows(entries: list) -> list[dict]:
    """One row per GLPI column: where it comes from and how it is converted."""
    rows = []
    for entry in entries:
        sources = []
        for source in entry.get("sources") or []:
            kind = source.get("from")
            sources.append(source.get("name") or kind or "")
        rows.append(
            {
                "column": entry.get("column"),
                "sources": sources,
                "transform": entry.get("transform", "text"),
                "itemtype": entry.get("itemtype", ""),
                "mandatory": bool(entry.get("mandatory")),
            }
        )
    return rows


def _safe_yaml(filename: str) -> dict | list:
    try:
        return load_yaml(filename)
    except ConfigError:
        return {}

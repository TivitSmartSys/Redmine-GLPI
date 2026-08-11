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

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

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


def create_app(db_path: str | None = None) -> Flask:
    """Build the app. Raises ConfigError when .env is incomplete."""
    settings = load_settings()
    messages.register_secrets(settings.secret_values())
    mapping = load_yaml("mapping.yml")

    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    app.config["MAPPING"] = mapping
    app.config["DB_PATH"] = db_path or str(config.DEFAULT_DB_PATH)
    app.config["JOBS"] = JobManager(settings, mapping, app.config["DB_PATH"])
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
        return render_template("index.html", ui=UI_STRINGS)

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
        filename = default_report_path(job.label).name
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
        return jsonify(
            {
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
                },
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

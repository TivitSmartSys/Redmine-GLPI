"""Deployment entrypoint: gunicorn loads this module as ``wsgi:app``.

The SQLite path comes from ``MIGRATION_DB_PATH``. When it is unset,
``create_app`` falls back to ``config.settings.DEFAULT_DB_PATH``.

``MIGRATION_REPORTS_DIR`` does the same for everything a run writes: the batch's
per-item reports, its ``resumo.txt`` and console transcript, and the progress
tab's cached reading. It defaults to ``reports`` **relative to the working
directory**, which on a hardened deployment is inside the application directory
and therefore read-only — that is not a hypothetical, it is what production
answered on 2026-09-10 (``[Errno 30] Read-only file system: 'reports'``). Point
it at the same persistent volume the database lives on.
"""

from __future__ import annotations

import os

from web.server import create_app

app = create_app(
    db_path=os.environ.get("MIGRATION_DB_PATH"),
    reports_dir=os.environ.get("MIGRATION_REPORTS_DIR"),
)

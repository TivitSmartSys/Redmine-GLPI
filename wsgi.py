"""Deployment entrypoint: gunicorn loads this module as ``wsgi:app``.

The SQLite path comes from ``MIGRATION_DB_PATH``. When it is unset,
``create_app`` falls back to ``config.settings.DEFAULT_DB_PATH``.
"""

from __future__ import annotations

import os

from web.server import create_app

app = create_app(db_path=os.environ.get("MIGRATION_DB_PATH"))

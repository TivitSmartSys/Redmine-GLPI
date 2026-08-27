"""The batch ledger: what a batch run has attempted, and how it went.

Deliberately a separate table from `migration_map`. That one answers "which
GLPI item does this Redmine id map to" and is consulted by apply_plan on every
node. This one answers "have we already tried this root in this run, and what
happened" - retry bookkeeping, which would corrupt the meaning of the other.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batch_item (
  run_id     TEXT    NOT NULL,
  issue_id   INTEGER NOT NULL,
  state      TEXT    NOT NULL,   -- 'pending' | 'ok' | 'failed' | 'skipped'
  detail     TEXT    NOT NULL DEFAULT '',
  queued_at  TEXT    NOT NULL,
  updated_at TEXT    NOT NULL,
  position   INTEGER NOT NULL,
  PRIMARY KEY (run_id, issue_id)
);
CREATE TABLE IF NOT EXISTS batch_run (
  run_id     TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  started_at TEXT NOT NULL
);
"""

STATE_PENDING = "pending"
STATE_OK = "ok"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"

# A failure is retried on --resume; a skip is not. A skip means the issue was
# already migrated, which no amount of retrying changes.
RETRYABLE = (STATE_PENDING, STATE_FAILED)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BatchLedger:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def start_run(self, label: str) -> str:
        run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self._conn.execute(
            "INSERT INTO batch_run (run_id, label, started_at) VALUES (?, ?, ?)",
            (run_id, label, _now()),
        )
        self._conn.commit()
        return run_id

    def queue(self, run_id: str, issue_ids: list[int]) -> None:
        now = _now()
        self._conn.executemany(
            """
            INSERT INTO batch_item
                (run_id, issue_id, state, detail, queued_at, updated_at, position)
            VALUES (?, ?, ?, '', ?, ?, ?)
            ON CONFLICT(run_id, issue_id) DO NOTHING
            """,
            [
                (run_id, int(issue_id), STATE_PENDING, now, now, position)
                for position, issue_id in enumerate(issue_ids)
            ],
        )
        self._conn.commit()

    def mark(self, run_id: str, issue_id: int, state: str, detail: str = "") -> None:
        self._conn.execute(
            "UPDATE batch_item SET state = ?, detail = ?, updated_at = ? "
            "WHERE run_id = ? AND issue_id = ?",
            (state, detail, _now(), run_id, int(issue_id)),
        )
        self._conn.commit()

    def pending(self, run_id: str) -> list[int]:
        """Never-tried first, then failures. A retry must not delay fresh work."""
        rows = self._conn.execute(
            """
            SELECT issue_id FROM batch_item
            WHERE run_id = ? AND state IN (?, ?)
            ORDER BY CASE state WHEN ? THEN 0 ELSE 1 END, position
            """,
            (run_id, STATE_PENDING, STATE_FAILED, STATE_PENDING),
        ).fetchall()
        return [int(row["issue_id"]) for row in rows]

    def counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM batch_item WHERE run_id = ? GROUP BY state",
            (run_id,),
        ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    def failures(self, run_id: str) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT issue_id, detail FROM batch_item "
            "WHERE run_id = ? AND state = ? ORDER BY position",
            (run_id, STATE_FAILED),
        ).fetchall()
        return [(int(row["issue_id"]), str(row["detail"])) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "BatchLedger":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

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

    def run_exists(self, run_id: str) -> bool:
        """Is this a run this ledger has ever started?

        --resume on an unknown id used to produce an empty queue and exit 0,
        which reads exactly like a completed run. The batch_run table is the
        only place that can tell the two apart.
        """
        row = self._conn.execute(
            "SELECT 1 FROM batch_run WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        return row is not None

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

    def runs(self) -> list[dict]:
        """Every run this ledger knows, newest first, with its state counts.

        For the panel's resume list. `pending()` answers what is left to do;
        it cannot say which project a run belonged to, nor distinguish a run
        that finished cleanly from one that died at item 900 - and those are
        exactly the two things an operator picks a run to resume by.

        A LEFT JOIN, not an inner one: a run that died before `queue()` has no
        batch_item rows at all, and dropping it from the list would hide the
        very failure the operator came looking for.

        Ordered by started_at then rowid, because start_run() stamps whole
        seconds - two runs started in the same second would otherwise come back
        in an arbitrary order.
        """
        rows = self._conn.execute(
            """
            SELECT r.run_id, r.label, r.started_at, i.state, COUNT(i.issue_id) AS n
            FROM batch_run r
            LEFT JOIN batch_item i ON i.run_id = r.run_id
            GROUP BY r.run_id, i.state
            ORDER BY r.started_at DESC, r.rowid DESC
            """
        ).fetchall()

        runs: dict[str, dict] = {}
        for row in rows:
            entry = runs.setdefault(
                row["run_id"],
                {
                    "run_id": row["run_id"],
                    "label": row["label"],
                    "started_at": row["started_at"],
                    "counts": {},
                },
            )
            # The LEFT JOIN yields one row with state NULL for an empty run.
            if row["state"] is not None:
                entry["counts"][str(row["state"])] = int(row["n"])

        result = list(runs.values())
        for entry in result:
            entry["total"] = sum(entry["counts"].values())
            entry["resumable"] = sum(
                entry["counts"].get(state, 0) for state in RETRYABLE
            )
        return result

    def run_label(self, run_id: str) -> str | None:
        """Which project a run belongs to, or None when the run is unknown."""
        row = self._conn.execute(
            "SELECT label FROM batch_run WHERE run_id = ?", (str(run_id),)
        ).fetchone()
        return str(row["label"]) if row is not None else None

    def items(self, run_id: str) -> list[dict]:
        """Every item of a run, in queue order, with its state and reason.

        This is what rebuilds the panel's progress table for a run that has
        already finished. The live table is built from events, which exist only
        while the process that produced them is alive; the ledger is the only
        durable record of which root ended up in which state and why.

        Queue order, not update order: it is the order the operator watched.
        """
        rows = self._conn.execute(
            "SELECT issue_id, state, detail FROM batch_item "
            "WHERE run_id = ? ORDER BY position",
            (str(run_id),),
        ).fetchall()
        return [
            {
                "issue_id": int(row["issue_id"]),
                "state": str(row["state"]),
                "detail": str(row["detail"] or ""),
            }
            for row in rows
        ]

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

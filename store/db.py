"""SQLite migration map (spec section 9.1).

Redmine and GLPI have independent auto-increment counters, so ids cannot and
need not match. This table is the local record of what was already created,
giving two things:

  * idempotence - checked before every POST, so a node is never created twice;
  * resumability - an interrupted run picks up where it stopped.

The authoritative deduplication check is still the `rdmfield` lookup in GLPI;
this table is a cache and a crash guard, not a replacement for it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# SQLite refuses a statement with more than SQLITE_MAX_VARIABLE_NUMBER bound
# parameters (999 on the builds that ship with CPython). A Faturamento tree can
# hold thousands of issues, so every IN (...) below is split into batches.
_CHUNK_SIZE = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_map (
  redmine_id        INTEGER NOT NULL,
  glpi_id           INTEGER NOT NULL,
  glpi_itemtype     TEXT    NOT NULL,   -- 'Project' | 'ProjectTask' | container itemtype
  parent_redmine_id INTEGER,
  status            TEXT    NOT NULL,   -- 'ok' | 'partial' | 'failed'
  migrated_at       TEXT    NOT NULL,
  PRIMARY KEY (redmine_id, glpi_itemtype)
);
"""

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


def _chunks(redmine_ids: Iterable[int]) -> list[list[int]]:
    """Deduplicated ids, in batches small enough to bind in one statement."""
    unique: list[int] = []
    seen: set[int] = set()
    for value in redmine_ids:
        number = int(value)
        if number not in seen:
            seen.add(number)
            unique.append(number)
    return [unique[i : i + _CHUNK_SIZE] for i in range(0, len(unique), _CHUNK_SIZE)]


@dataclass(frozen=True)
class MigrationEntry:
    redmine_id: int
    glpi_id: int
    glpi_itemtype: str
    parent_redmine_id: int | None
    status: str
    migrated_at: str


class MigrationStore:
    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def lookup(self, redmine_id: int, glpi_itemtype: str) -> MigrationEntry | None:
        row = self._conn.execute(
            "SELECT * FROM migration_map WHERE redmine_id = ? AND glpi_itemtype = ?",
            (int(redmine_id), glpi_itemtype),
        ).fetchone()
        return self._to_entry(row) if row else None

    def record(
        self,
        redmine_id: int,
        glpi_id: int,
        glpi_itemtype: str,
        parent_redmine_id: int | None = None,
        status: str = STATUS_OK,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO migration_map
                (redmine_id, glpi_id, glpi_itemtype, parent_redmine_id, status, migrated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(redmine_id, glpi_itemtype) DO UPDATE SET
                glpi_id = excluded.glpi_id,
                parent_redmine_id = excluded.parent_redmine_id,
                status = excluded.status,
                migrated_at = excluded.migrated_at
            """,
            (
                int(redmine_id),
                int(glpi_id),
                glpi_itemtype,
                int(parent_redmine_id) if parent_redmine_id is not None else None,
                status,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def all_entries(self, limit: int = 1000) -> list[MigrationEntry]:
        """Everything recorded so far, newest first.

        The table had no listing surface: `lookup` answers about one node and
        `entries_for_root` about one tree, so there was no way to see what had
        already been migrated. The web panel needs exactly that.
        """
        rows = self._conn.execute(
            "SELECT * FROM migration_map ORDER BY migrated_at DESC, redmine_id DESC "
            "LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [self._to_entry(row) for row in rows]

    def entries_for_root(self, root_redmine_id: int) -> list[MigrationEntry]:
        # The second predicate used to read `parent_redmine_id IS NOT NULL`,
        # which is unbound and matched every row that has any parent - i.e. other
        # trees as well. Harmless while nothing called this, fatal the moment a
        # delete is built on top of it.
        rows = self._conn.execute(
            """
            SELECT * FROM migration_map
            WHERE redmine_id = ? OR parent_redmine_id = ?
            ORDER BY migrated_at
            """,
            (int(root_redmine_id), int(root_redmine_id)),
        ).fetchall()
        return [self._to_entry(row) for row in rows]

    def entries_for_ids(self, redmine_ids: Iterable[int]) -> list[MigrationEntry]:
        """Every row belonging to the given issues, whatever the itemtype.

        The table has no root column and Faturamento rows are recorded with
        `parent_redmine_id = NULL`, so a tree cannot be reconstructed from the
        table alone - the caller passes the ids it read from Redmine.
        """
        found: list[MigrationEntry] = []
        for chunk in _chunks(redmine_ids):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT * FROM migration_map WHERE redmine_id IN ({placeholders}) "
                "ORDER BY migrated_at",
                chunk,
            ).fetchall()
            found.extend(self._to_entry(row) for row in rows)
        return found

    def delete_for_ids(self, redmine_ids: Iterable[int]) -> int:
        """Forget the given issues. Returns how many rows were removed.

        Used by reset_migration.py so an issue whose GLPI project was deleted
        can be migrated again: without this the local map keeps pointing tasks
        at GLPI ids that no longer exist and apply_plan skips them.
        """
        removed = 0
        for chunk in _chunks(redmine_ids):
            placeholders = ",".join("?" * len(chunk))
            cursor = self._conn.execute(
                f"DELETE FROM migration_map WHERE redmine_id IN ({placeholders})",
                chunk,
            )
            removed += cursor.rowcount
        self._conn.commit()
        return removed

    @staticmethod
    def _to_entry(row: sqlite3.Row) -> MigrationEntry:
        return MigrationEntry(
            redmine_id=row["redmine_id"],
            glpi_id=row["glpi_id"],
            glpi_itemtype=row["glpi_itemtype"],
            parent_redmine_id=row["parent_redmine_id"],
            status=row["status"],
            migrated_at=row["migrated_at"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MigrationStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

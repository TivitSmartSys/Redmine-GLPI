"""The batch loop: ordering, bookkeeping and failure isolation.

This module deliberately contains NO migration logic. One item is exactly the
existing `main.py` pipeline - build_project_plan then apply_plan - so every
behaviour recorded in CLAUDE.md (entities, containers, notes, attachments,
creation dates, the VARCHAR(255) truncation) applies unchanged and is not
re-implemented here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from clients.errors import ApiError
from main import apply_plan, build_project_plan, check_already_migrated
from report import messages
from report.reporter import Reporter
from store.batch import STATE_FAILED, STATE_OK, STATE_SKIPPED
from store.db import MigrationStore


def render_item_report(plan, apply_mode: bool) -> str:
    return Reporter(plan, apply_mode=apply_mode).render()


def run_batch(
    glpi,
    redmine,
    mapping: dict,
    ledger,
    run_id: str,
    issue_ids: list[int],
    *,
    apply_mode: bool,
    report_dir,
    db_path: str = "migration.db",
    skip_attachments: bool = False,
    skip_notes: bool = False,
) -> None:
    """Migrate each root in turn. A failure is recorded, never fatal.

    Only two things end a run early: a dead GLPI session and a KeyboardInterrupt.
    Everything else - a missing entity, an oversized attachment, a 403 on one
    Redmine issue - belongs to its item and is written to the ledger.
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    total = len(issue_ids)

    store = MigrationStore(db_path) if apply_mode else None
    try:
        for position, issue_id in enumerate(issue_ids, start=1):
            print(messages.BATCH_ITEM_START.format(
                position=position, total=total, issue_id=issue_id
            ))
            try:
                existing = check_already_migrated(glpi, issue_id)
                if existing:
                    ledger.mark(
                        run_id, issue_id, STATE_SKIPPED,
                        messages.BATCH_ITEM_ALREADY.format(glpi_id=existing),
                    )
                    continue

                plan = build_project_plan(
                    glpi, redmine, mapping, issue_id,
                    skip_attachments=skip_attachments, skip_notes=skip_notes,
                )
                if apply_mode:
                    apply_plan(glpi, plan, store, redmine=redmine)

                (report_dir / f"RDM{issue_id}.txt").write_text(
                    render_item_report(plan, apply_mode), encoding="utf-8"
                )
                ledger.mark(run_id, issue_id, STATE_OK)
            except KeyboardInterrupt:
                # Leave the item pending so --resume picks it up unchanged.
                raise
            except Exception as exc:  # noqa: BLE001 - one item must not end the run
                detail = messages.redact(exc)
                print(
                    messages.BATCH_ITEM_FAILED.format(issue_id=issue_id, detail=detail),
                    file=sys.stderr,
                )
                ledger.mark(run_id, issue_id, STATE_FAILED, str(detail)[:500])
    finally:
        if store is not None:
            store.close()

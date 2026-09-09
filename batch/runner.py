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

from clients.errors import ApiError  # noqa: F401 - re-exported for callers
from main import apply_plan, build_project_plan, check_already_migrated
from report import messages
from report.reporter import Reporter
from store.batch import STATE_FAILED, STATE_OK, STATE_SKIPPED
from store.db import MigrationStore

# How many failures in a row end the run.
#
# The module docstring and the design doc both promise that a dead GLPI session
# stops the batch; nothing implemented it. The failure mode is silent and
# expensive: the session dies at item 900 of 5451 and the remaining 4551 each
# fail fast, landing in the ledger as genuine migration failures indistinguishable
# from a 33 MB attachment or a 403 on one issue - and the run still exits 0.
#
# Ten is chosen against the measured data rather than as a round number: real
# per-item failures are sparse and unrelated to each other (an oversized file, a
# missing entity, one 403), so ten consecutive ones is not a run of bad luck, it
# is an environment that stopped answering. The remaining items are left
# `pending`, so --resume picks them up untouched once the session is back.
MAX_CONSECUTIVE_FAILURES = 10


def _notify(on_item, position: int, total: int, issue_id: int, state: str, detail: str) -> None:
    """Feed the progress callback without ever letting it end a run.

    Swallowing is not laziness here. The callback fires AFTER the ledger is
    marked, i.e. after the item has been written to GLPI; letting it raise
    would hand the exception to the broad `except` in the loop, which would
    then record a committed migration as `failed` and hand it to --resume for
    a second, duplicating attempt. A broken progress display is a display bug.
    """
    if on_item is None:
        return
    try:
        on_item(
            position=position, total=total, issue_id=issue_id,
            state=state, detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 - see above
        print(
            messages.BATCH_PROGRESS_CALLBACK_FAILED.format(
                issue_id=issue_id, detail=messages.redact(exc)
            ),
            file=sys.stderr,
        )


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
    on_item=None,
) -> None:
    """Migrate each root in turn. A failure is recorded, never fatal.

    Only three things end a run early: a KeyboardInterrupt, and - since the fix
    wave of 2026-08-27 - MAX_CONSECUTIVE_FAILURES failures in a row, which is
    how a dead GLPI session announces itself. Everything else - a missing
    entity, an oversized attachment, a 403 on one Redmine issue - belongs to its
    item and is written to the ledger.

    `on_item(position=, total=, issue_id=, state=, detail=)` is an optional
    progress feed for the web panel, called once per item right after the
    ledger is marked. It is deliberately a callback rather than something the
    caller parses out of stdout: the printed text is the report, and the report
    is the primary functional requirement - a reworded message must never be
    able to break a progress display. Every call is wrapped, because by the
    time it fires the item is already committed to GLPI (see _notify).
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    total = len(issue_ids)

    store = MigrationStore(db_path) if apply_mode else None
    consecutive_failures = 0
    try:
        for position, issue_id in enumerate(issue_ids, start=1):
            print(messages.BATCH_ITEM_START.format(
                position=position, total=total, issue_id=issue_id
            ))
            try:
                existing = check_already_migrated(glpi, issue_id)
                if existing:
                    detail = messages.BATCH_ITEM_ALREADY.format(glpi_id=existing)
                    ledger.mark(run_id, issue_id, STATE_SKIPPED, detail)
                    _notify(on_item, position, total, issue_id, STATE_SKIPPED, detail)
                    consecutive_failures = 0
                    continue

                plan = build_project_plan(
                    glpi, redmine, mapping, issue_id,
                    skip_attachments=skip_attachments, skip_notes=skip_notes,
                )
                if apply_mode:
                    apply_plan(glpi, plan, store, redmine=redmine)
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
                _notify(on_item, position, total, issue_id, STATE_FAILED, str(detail)[:500])
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # Stop the loop. Everything still queued keeps its `pending`
                    # state - it is never marked - so --resume retries it
                    # untouched instead of inheriting a fabricated failure.
                    print(
                        messages.BATCH_ABORTED_CONSECUTIVE.format(
                            count=consecutive_failures, run_id=run_id
                        ),
                        file=sys.stderr,
                    )
                    return
                continue

            consecutive_failures = 0
            ledger.mark(run_id, issue_id, STATE_OK)
            _notify(on_item, position, total, issue_id, STATE_OK, "")

            # The report file is written OUTSIDE the pipeline's try on purpose.
            # A disk-full mid-run would otherwise be caught by the broad except
            # above and mark every remaining item `failed`, indistinguishable in
            # the ledger from a real migration failure - while the migration for
            # this item had in fact succeeded and been committed to GLPI.
            path = report_dir / f"RDM{issue_id}.txt"
            try:
                path.write_text(render_item_report(plan, apply_mode), encoding="utf-8")
            except OSError as exc:
                print(
                    messages.BATCH_REPORT_WRITE_FAILED.format(
                        issue_id=issue_id, path=path, detail=messages.redact(exc)
                    ),
                    file=sys.stderr,
                )
    finally:
        if store is not None:
            store.close()

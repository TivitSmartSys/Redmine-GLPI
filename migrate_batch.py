"""Phase 1: migrate a whole Redmine project in supervised, resumable batches.

    python migrate_batch.py --project hydro                  # dry-run
    python migrate_batch.py --project hydro --apply
    python migrate_batch.py --project projetos-telecom --apply --limit 200
    python migrate_batch.py --project projetos-telecom --apply --resume <run-id>

Run order is smallest first - operacao-cemig (4), hydro (170), then
projetos-telecom (5451). The first four cost minutes and show whether phase 0
was done right; starting with Telecom checks the same thing with a 5451-item
bill.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from batch.report import render_summary
from batch.runner import run_batch
from batch.selection import REDMINE_PROJECTS, pending_roots
from clients.errors import ApiError
from clients.glpi import GlpiClient
from clients.redmine import RedmineClient
from config.settings import DEFAULT_DB_PATH, ConfigError, load_settings, load_yaml
from main import run_preflight
from report import messages
from store.batch import BatchLedger

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_batch.py", description=messages.CLI_HELP_BATCH
    )
    parser.add_argument(
        "--project", required=True, choices=sorted(REDMINE_PROJECTS),
        help=messages.CLI_HELP_BATCH_PROJECT,
    )
    parser.add_argument("--apply", action="store_true", help=messages.CLI_HELP_APPLY)
    parser.add_argument("--yes", action="store_true", help=messages.CLI_HELP_YES)
    parser.add_argument("--limit", type=int, default=None, help=messages.CLI_HELP_BATCH_LIMIT)
    parser.add_argument("--resume", default=False, help=messages.CLI_HELP_BATCH_RESUME)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help=messages.CLI_HELP_DB)
    parser.add_argument("--reports", default="reports", help=messages.CLI_HELP_BATCH_REPORTS)
    parser.add_argument("--purge-record", default=None, help=messages.CLI_HELP_BATCH_PURGE_RECORD)
    parser.add_argument("--skip-attachments", action="store_true", help=messages.CLI_HELP_SKIP_ATTACHMENTS)
    parser.add_argument("--skip-notes", action="store_true", help=messages.CLI_HELP_SKIP_NOTES)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        mapping = load_yaml("mapping.yml")
    except ConfigError as exc:
        print(messages.CONFIG_MISSING_VARS.format(names=str(exc)), file=sys.stderr)
        return EXIT_CONFIG
    messages.register_secrets(settings.secret_values())

    tracker = REDMINE_PROJECTS[args.project]
    print(messages.CLI_MODE_APPLY if args.apply else messages.CLI_MODE_DRY_RUN)

    try:
        with GlpiClient(
            settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
        ) as glpi, RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
            # Preflight ONCE for the whole batch, not per item: it opens the
            # session, widens the entity tree and loads every dropdown
            # dictionary. Repeating that 5451 times is hours of pure waste.
            if not run_preflight(glpi, redmine, mapping, issue_id=None):
                print(messages.PREFLIGHT_ABORTED, file=sys.stderr)
                return EXIT_FAILED

            with BatchLedger(args.db) as ledger:
                if args.resume:
                    run_id = str(args.resume)
                    queue = ledger.pending(run_id)
                else:
                    run_id = ledger.start_run(args.project)
                    queue = pending_roots(glpi, redmine, tracker, limit=args.limit)
                    ledger.queue(run_id, queue)

                print(messages.BATCH_QUEUE.format(count=len(queue), run_id=run_id))
                if not queue:
                    print(messages.BATCH_NOTHING_TO_DO)
                    return EXIT_OK

                if args.apply and not (args.yes or _confirm(len(queue))):
                    print(messages.APPLY_CANCELLED)
                    return EXIT_OK

                report_dir = Path(args.reports) / run_id
                run_batch(
                    glpi, redmine, mapping, ledger, run_id, queue,
                    apply_mode=args.apply, report_dir=report_dir, db_path=args.db,
                    skip_attachments=args.skip_attachments, skip_notes=args.skip_notes,
                )

                purge_record = None
                if args.purge_record and Path(args.purge_record).exists():
                    purge_record = Path(args.purge_record).read_text(encoding="utf-8")

                summary = render_summary(ledger, run_id, args.project, purge_record)
                summary_path = report_dir / "resumo.txt"
                summary_path.write_text(summary, encoding="utf-8")
                print()
                print(summary)
                print(messages.BATCH_SUMMARY_SAVED.format(path=summary_path))
                return EXIT_OK
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return EXIT_FAILED
    except KeyboardInterrupt:
        print(messages.CLI_INTERRUPTED, file=sys.stderr)
        return EXIT_FAILED


def _confirm(count: int) -> bool:
    try:
        answer = input(messages.BATCH_CONFIRM_PROMPT.format(count=count))
    except EOFError:
        return False
    return answer.strip().casefold() in messages.APPLY_CONFIRM_ACCEPT


if __name__ == "__main__":
    raise SystemExit(main())

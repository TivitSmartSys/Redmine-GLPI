"""The run-level report. Sums the items; never replaces their own reports.

Spec 13 rule 7 says nothing disappears silently. The per-item reports prove no
FIELD vanished. This one proves no ITEM vanished: the four state counts add up
to the number queued, and every failure is printed with its reason rather than
tallied.
"""

from __future__ import annotations

from report import messages
from store.batch import STATE_FAILED, STATE_OK, STATE_PENDING, STATE_SKIPPED

_ORDER = (STATE_OK, STATE_SKIPPED, STATE_FAILED, STATE_PENDING)


def render_summary(ledger, run_id: str, label: str, purge_record: str | None = None) -> str:
    counts = ledger.counts(run_id)
    total = sum(counts.values())

    lines = [
        messages.BATCH_SUMMARY_HEADER.format(label=label, run_id=run_id),
        "=" * 60,
        "",
        messages.BATCH_SUMMARY_TOTAL.format(total=total),
    ]
    for state in _ORDER:
        lines.append(
            messages.BATCH_SUMMARY_STATE.format(state=state, count=counts.get(state, 0))
        )

    failures = ledger.failures(run_id)
    lines.append("")
    if failures:
        lines.append(messages.BATCH_SUMMARY_FAILURES.format(count=len(failures)))
        for issue_id, detail in failures:
            lines.append(
                messages.BATCH_SUMMARY_FAILURE_LINE.format(
                    issue_id=issue_id, detail=detail or "-"
                )
            )
    else:
        lines.append(messages.BATCH_SUMMARY_NO_FAILURES)

    if purge_record:
        lines.extend(["", messages.BATCH_SUMMARY_PURGE_HEADER, "", purge_record])

    return "\n".join(lines)

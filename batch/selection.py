"""Which roots still need migrating.

Measured 2026-08-27: Redmine has exactly four projects, and every in-scope root
is parentless. Three projects hold roots; "Configuração REDE CORP VOIP" holds no
issues at all, of any tracker, so it has no entry here.
"""

from __future__ import annotations

from config.settings import ITEMTYPE_ADDITIONAL_FIELDS

# Redmine project identifier -> the root tracker whose issues live in it.
# Each project holds exactly one root tracker; the asymmetry is measured, not
# assumed (tracker 39 is a root tracker only, tracker 40 a task tracker only).
REDMINE_PROJECTS = {
    "projetos-telecom": 14,   # Área de Telecom  - 5451 roots
    "hydro": 42,              # HYDRO            -  170 roots
    "operacao-cemig": 39,     # Operação CEMIG   -    6 roots
}


def migrated_markers(glpi) -> set[int]:
    """Every Redmine id GLPI already claims, from ONE bulk read.

    One read, not one search per root: at 5627 roots the per-issue path is the
    difference between a minute and an hour before any work starts. Non-numeric
    markers are ignored - the poisoned import left values like 'RDM123' and an
    ISO timestamp behind.
    """
    found: set[int] = set()
    for row in glpi.iter_all_rows(ITEMTYPE_ADDITIONAL_FIELDS):
        value = str(row.get("rdmfield") or "").strip()
        if value.isdigit():
            found.add(int(value))
    return found


def candidate_roots(redmine, tracker_id: int) -> list[int]:
    """Parentless issues of one tracker, in Redmine's own order."""
    return [
        int(issue["id"])
        for issue in redmine.iter_issues(tracker_id)
        if not issue.get("parent")
    ]


def pending_roots(glpi, redmine, tracker_id: int, limit: int | None = None) -> list[int]:
    already = migrated_markers(glpi)
    pending = [rid for rid in candidate_roots(redmine, tracker_id) if rid not in already]
    return pending[:limit] if limit else pending

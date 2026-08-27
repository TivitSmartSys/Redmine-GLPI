"""Phase 0 of the batch migration: remove the poisoned 2026-06-06 import.

Why this exists (measured 2026-08-27, see the design doc): GLPI holds 1274
projects, 1253 of them created on 2026-06-06 by a bulk import that is not this
tool. That import wrote `rdmfield` misaligned - 698 markers contradict the RDM
number in their own project's name, 5 agree. One marker sits on 13 unrelated
projects. A further 344 of those projects carry no container-15 row at all.

The consequence is that dedup is broken in both directions at once: ~660 issues
would be skipped because their marker sits on someone else's project, and the
1253 cascas would be migrated a second time because their true issue cannot be
found by marker. Repairing the markers was rejected - the cascas hold no tasks,
no notes and no documents, so repair would leave them indistinguishable from
complete migrations.

Dry-run by default, like every other writer in this repo.
"""

from __future__ import annotations

# Projects created on this date belong to the poisoned bulk import.
IMPORT_DATE = "2026-06-06"

# Throwaways from manual testing. Some postdate the import; 1263 is named
# "RDM 16467 - ..." and would read as a real migration if left behind.
TEST_PROJECT_IDS = frozenset(
    {1256, 1257, 1258, 1259, 1260, 1261, 1262, 1263, 1264, 1267, 1291, 1295}
)

# Verified individually on 2026-08-27: each carries an rdmfield pointing at a
# real in-scope root whose subject matches the project name.
#   1286 -> RDM 20438 (t14)   1287 -> 20472 (t14)   1288 -> 2101  (t14)
#   1289 -> RDM 20280 (t14)   1290 -> 19533 (t14)   1292 -> 19074 (t39)
#   1293 -> RDM 18729 (t39)   1296 -> 20556 (t14)   1298 -> 15815 (t14)
# 1298 reads date_creation 2023-09-20 because it is the test of the date-shift
# feature added 2026-08-27, not because it predates the migration.
KEEP_PROJECT_IDS = frozenset({1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298})


class KeepListViolation(Exception):
    """A project that must survive was selected for purging."""


def select_targets(projects: list[dict]) -> list[int]:
    """Ids to purge, chosen positively - never as "everything else".

    Raises KeepListViolation rather than silently dropping a protected id: if
    the rule ever selects one, the rule is wrong and the operator must see it
    before anything is deleted.
    """
    targets: list[int] = []
    for row in projects:
        pid = int(row.get("id") or 0)
        if not pid:
            continue
        created = str(row.get("date_creation") or "")[:10]
        if created == IMPORT_DATE or pid in TEST_PROJECT_IDS:
            targets.append(pid)

    protected = sorted(set(targets) & KEEP_PROJECT_IDS)
    if protected:
        raise KeepListViolation(
            "projetos protegidos entraram no alvo: "
            + ", ".join(str(p) for p in protected)
        )
    return targets

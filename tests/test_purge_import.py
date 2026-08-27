"""Phase 0 selects its targets positively and refuses to touch the keep list.

The keep list is not arbitrary: each of those nine projects was verified on
2026-08-27 to carry an rdmfield pointing at a real in-scope Redmine root whose
subject matches the project name. Everything else with a 2026-06-06 creation
date is a casca from the poisoned bulk import.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from purge_import import (  # noqa: E402
    IMPORT_DATE,
    KEEP_PROJECT_IDS,
    KeepListViolation,
    select_targets,
)


def project(pid, created="2026-06-06 15:40:00"):
    return {"id": pid, "date_creation": created, "name": f"P{pid}"}


def test_selects_every_project_created_on_the_import_date():
    projects = [project(100), project(101), project(1286, "2026-08-12 14:54:23")]

    assert select_targets(projects) == [100, 101]


def test_selects_the_explicit_test_ids_whatever_their_date():
    projects = [project(1291, "2026-08-19 10:58:33"), project(1295, "2026-08-25 11:05:44")]

    assert select_targets(projects) == [1291, 1295]


def test_never_selects_a_keep_list_project():
    projects = [project(pid) for pid in sorted(KEEP_PROJECT_IDS)]

    with pytest.raises(KeepListViolation) as exc:
        select_targets(projects)

    assert "1286" in str(exc.value)


def test_keep_list_holds_exactly_the_nine_verified_projects():
    assert KEEP_PROJECT_IDS == frozenset(
        {1286, 1287, 1288, 1289, 1290, 1292, 1293, 1296, 1298}
    )


def test_import_date_is_the_measured_one():
    assert IMPORT_DATE == "2026-06-06"


def test_a_project_with_no_creation_date_is_left_alone():
    assert select_targets([{"id": 500, "date_creation": None, "name": "?"}]) == []

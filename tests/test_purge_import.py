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

from config.settings import ITEMTYPE_ADDITIONAL_FIELDS as CONTAINER  # noqa: E402
from report import messages  # noqa: E402
from purge_import import (  # noqa: E402
    IMPORT_DATE,
    KEEP_PROJECT_IDS,
    KeepListViolation,
    PurgeTarget,
    build_parser,
    build_purge_plan,
    render_purge_report,
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


class FakeGlpi:
    """Answers iter_all_rows for Project and the container-15 itemtype."""

    def __init__(self, projects, container_rows):
        self._data = {"Project": projects, CONTAINER: container_rows}
        self.widened = False

    def set_active_entity_root(self):
        self.widened = True

    def iter_all_rows(self, itemtype, page_size=200):
        return list(self._data.get(itemtype, []))


def test_plan_pairs_each_target_with_its_container_rows():
    glpi = FakeGlpi(
        projects=[project(100), project(1286, "2026-08-12 14:54:23")],
        container_rows=[
            {"id": 7, "items_id": 100, "rdmfield": "17343"},
            {"id": 8, "items_id": 100, "rdmfield": ""},
            {"id": 9, "items_id": 1286, "rdmfield": "20438"},
        ],
    )

    plan = build_purge_plan(glpi)

    assert [t.project_id for t in plan] == [100]
    assert plan[0].container_row_ids == [7, 8]
    assert plan[0].marker == "17343"


def test_plan_widens_the_entity_session_before_reading():
    glpi = FakeGlpi(projects=[project(100)], container_rows=[])

    build_purge_plan(glpi)

    assert glpi.widened, "a narrowed session cannot see projects in other entities"


def test_report_names_every_target_and_the_kept_count():
    targets = [PurgeTarget(100, "Casca", 0, [7], "17343")]

    text = render_purge_report(targets, applied=False)

    assert "100" in text and "Casca" in text and "17343" in text
    assert "1" in text


def test_dry_run_is_the_default():
    assert build_parser().parse_args([]).apply is False


def test_report_header_distinguishes_planned_from_applied():
    """CONTROLLER RULING (2026-08-27): the saved report is evidence of a
    destructive operation, so it must say on its face whether it describes an
    intention or an action. The two headers must differ, and each must appear
    for its own value of `applied` - never the other one."""
    targets = [PurgeTarget(100, "Casca", 0, [7], "17343")]

    planned = render_purge_report(targets, applied=False)
    applied = render_purge_report(targets, applied=True)

    assert messages.PURGE_HEADER_PLANNED != messages.PURGE_HEADER_APPLIED
    assert messages.PURGE_HEADER_PLANNED in planned
    assert messages.PURGE_HEADER_APPLIED not in planned
    assert messages.PURGE_HEADER_APPLIED in applied
    assert messages.PURGE_HEADER_PLANNED not in applied

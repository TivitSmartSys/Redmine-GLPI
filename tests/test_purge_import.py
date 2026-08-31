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

import purge_import  # noqa: E402
from config.settings import ITEMTYPE_ADDITIONAL_FIELDS as CONTAINER  # noqa: E402
from config.settings import ITEMTYPE_FATURAMENTO  # noqa: E402
from report import messages  # noqa: E402
from purge_import import (  # noqa: E402
    IMPORT_DATE,
    KEEP_PROJECT_IDS,
    MAX_IMPORT_PROJECT_ID,
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


# -- I6: date_creation is back-dated since 2026-08-27; an id ceiling protects
# real migrations that happen to have been created on 2026-06-06 in Redmine ---


def test_a_backdated_real_migration_above_the_ceiling_is_not_selected():
    """A Redmine root created 2026-06-06 now back-dates its GLPI project's
    date_creation to match - exactly IMPORT_DATE - so the date rule alone
    would purge a real migration. The id ceiling is what keeps it safe."""
    projects = [project(1500, "2026-06-06 09:00:00")]

    assert select_targets(projects) == []


def test_a_casca_at_the_ceiling_id_is_still_selected(monkeypatch):
    # 1298 itself is a real kept project (KEEP_PROJECT_IDS), so the boundary
    # is exercised against a fresh ceiling instead of colliding with it.
    monkeypatch.setattr(purge_import, "MAX_IMPORT_PROJECT_ID", 2000)
    projects = [project(2000, "2026-06-06 09:00:00")]

    assert select_targets(projects) == [2000]


def test_a_casca_just_below_the_ceiling_is_still_selected():
    projects = [project(MAX_IMPORT_PROJECT_ID - 1, "2026-06-06 09:00:00")]

    assert select_targets(projects) == [MAX_IMPORT_PROJECT_ID - 1]


def test_explicit_test_ids_are_unaffected_by_the_ceiling():
    """TEST_PROJECT_IDS stays an exact-match rule regardless of id."""
    projects = [project(1291, "2026-08-19 10:58:33")]  # far above the ceiling

    assert select_targets(projects) == [1291]


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


def test_plan_selects_orphan_container_rows_whose_host_project_is_gone():
    """A container-15 row whose host project no longer exists is PRECISELY
    the poison this tool exists to remove: it keeps answering find_by_rdmfield
    for a project that is gone. It was previously never selected because
    build_purge_plan only looked at rows whose items_id was in the target set."""
    glpi = FakeGlpi(
        projects=[project(100)],
        container_rows=[
            {"id": 7, "items_id": 100, "rdmfield": "17343"},
            {"id": 50, "items_id": 9999, "rdmfield": "orphan-marker"},
        ],
    )

    plan = build_purge_plan(glpi)

    orphans = [t for t in plan if t.is_orphan]
    assert len(orphans) == 1
    assert orphans[0].container_row_ids == [50]
    assert orphans[0].project_id == 9999
    assert orphans[0].marker == "orphan-marker"


def test_plan_does_not_touch_a_container_row_of_a_live_kept_project():
    """A row whose host project is alive but NOT a purge target (a kept,
    still-in-use project) must be selected as neither a target nor an orphan."""
    glpi = FakeGlpi(
        projects=[project(100), project(555, "2026-08-12 14:00:00")],
        container_rows=[
            {"id": 7, "items_id": 100, "rdmfield": "17343"},
            {"id": 20, "items_id": 555, "rdmfield": "keep-me"},
        ],
    )

    plan = build_purge_plan(glpi)

    all_row_ids = {rid for t in plan for rid in t.container_row_ids}
    assert 20 not in all_row_ids


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


from purge_import import PurgeCounts, purge_one, verify_purge  # noqa: E402


class RecordingGlpi(FakeGlpi):
    def __init__(self, projects=(), container_rows=(), fail_on=None,
                 tasks=(), container26=None):
        super().__init__(list(projects), list(container_rows))
        self.deleted = []
        self.fail_on = fail_on or set()
        self.tasks = list(tasks)
        self.container26 = dict(container26 or {})

    def delete_item(self, itemtype, item_id, force_purge=True):
        if (itemtype, item_id) in self.fail_on:
            from clients.errors import GlpiError

            raise GlpiError("recusado")
        assert force_purge is True, "phase 0 purges; the trash keeps the marker alive"
        self.deleted.append((itemtype, item_id))

    def notepad_rows(self, itemtype, items_id):
        return [{"id": 55}]

    def document_links(self, itemtype, items_id):
        return [{"id": 66}]

    def get_container_rows(self, itemtype, items_id):
        return list(self.container26.get(items_id, []))

    def project_tasks(self, project_id):
        return list(self.tasks)


def test_container_row_is_deleted_before_its_project():
    glpi = RecordingGlpi()
    target = PurgeTarget(100, "Casca", 0, [7], "17343")

    purge_one(glpi, target)

    itemtypes = [d[0] for d in glpi.deleted]
    assert itemtypes.index(CONTAINER) < itemtypes.index("Project"), (
        "deleting the project first leaves an orphan marker - the exact poison "
        "this phase removes"
    )


def test_purge_one_counts_everything_it_removed():
    glpi = RecordingGlpi()

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7, 8], "17343"))

    assert counts.projects == 1
    assert counts.containers == 2
    assert counts.notes == 1
    assert counts.links == 1
    assert counts.failed == 0


def test_a_failed_container_delete_does_not_delete_the_project():
    glpi = RecordingGlpi(fail_on={(CONTAINER, 7)})

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert counts.failed == 1
    assert ("Project", 100) not in glpi.deleted, (
        "the project must survive so the operator can retry; the reverse order "
        "would strand the marker"
    )


def test_verify_counts_what_survived():
    glpi = FakeGlpi(
        projects=[project(1286, "2026-08-12 14:54:23")],
        container_rows=[{"id": 9, "items_id": 1286, "rdmfield": "20438"}],
    )

    assert verify_purge(glpi) == (1, 1, 0)


# -- I5(b): verify_purge must surface stray (orphan) rows, not just projects -


def test_verify_reports_stray_rows_whose_host_project_no_longer_exists():
    """A surviving project count alone says nothing about the markers: an
    orphan container row answers find_by_rdmfield exactly as a live one does,
    so a run that left orphans behind has not actually fixed dedup."""
    glpi = FakeGlpi(
        projects=[project(1286, "2026-08-12 14:54:23")],
        container_rows=[
            {"id": 9, "items_id": 1286, "rdmfield": "20438"},
            {"id": 99, "items_id": 42, "rdmfield": "leftover-orphan"},
        ],
    )

    assert verify_purge(glpi) == (1, 2, 1)


# -- I4: ProjectTasks and their container-26 rows must be purged too ---------


def test_purge_one_removes_projecttasks_and_their_container26_rows():
    glpi = RecordingGlpi(
        tasks=[{"id": 14109}],
        container26={14109: [{"id": 5001}]},
    )

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert counts.tasks == 1
    assert (ITEMTYPE_FATURAMENTO, 5001) in glpi.deleted
    assert ("ProjectTask", 14109) in glpi.deleted
    # Order: the container-26 row before its task, both before the project -
    # the same inversion as the container-15/project rule, one level down.
    assert glpi.deleted.index((ITEMTYPE_FATURAMENTO, 5001)) < glpi.deleted.index(
        ("ProjectTask", 14109)
    )
    assert glpi.deleted.index(("ProjectTask", 14109)) < glpi.deleted.index(
        ("Project", 100)
    )


def test_purge_one_is_best_effort_when_no_tasks_are_found():
    """A flat GET /ProjectTask was measured returning 0 rows for this whole
    instance; an empty read must not be treated as a failure."""
    glpi = RecordingGlpi(tasks=[])

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert counts.failed == 0
    assert counts.tasks == 0
    assert counts.projects == 1


def test_purge_one_swallows_a_projecttask_read_failure():
    """BEST EFFORT means a real API failure reading tasks must not abort the
    whole purge of this project - it is a nice-to-have, not the load-bearing
    part of the phase."""
    from clients.errors import ApiError

    class RaisingGlpi(RecordingGlpi):
        def project_tasks(self, project_id):
            raise ApiError("indisponível")

    glpi = RaisingGlpi()

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert counts.failed == 0
    assert counts.projects == 1


def test_a_stranded_container26_row_keeps_its_task_alive():
    """Same inversion as container 15: if a container-26 row refuses to
    delete, the task it belongs to must survive so the row is not orphaned."""
    glpi = RecordingGlpi(
        tasks=[{"id": 14109}],
        container26={14109: [{"id": 5001}]},
        fail_on={(ITEMTYPE_FATURAMENTO, 5001)},
    )

    counts = purge_one(glpi, PurgeTarget(100, "Casca", 0, [7], "17343"))

    assert ("ProjectTask", 14109) not in glpi.deleted
    assert counts.tasks == 0
    assert counts.failed == 1
    # The project purge itself still proceeds - only the stranded task's
    # branch is skipped.
    assert ("Project", 100) in glpi.deleted


def test_orphan_target_purges_only_its_rows_no_project_to_delete():
    glpi = RecordingGlpi()
    target = PurgeTarget(
        project_id=9999, name="", entities_id=0, container_row_ids=[50],
        marker="orphan-marker", is_orphan=True,
    )

    counts = purge_one(glpi, target)

    assert glpi.deleted == [(CONTAINER, 50)]
    assert counts.containers == 1
    assert counts.projects == 0
    assert counts.tasks == 0


# -- M10: an exception surfaced from a keep-list violation must be redacted --


def test_keep_violation_detail_is_redacted(monkeypatch, capsys):
    secret = "shh-secret-token-m10"
    messages.register_secrets((secret,))

    class FakeSettings:
        glpi_url = "http://glpi"
        glpi_user_token = secret
        glpi_app_token = "app"

        def secret_values(self):
            return (self.glpi_user_token,)

    class FakeGlpiCtx:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def boom_plan(_glpi):
        raise KeepListViolation(f"token exposed: {secret}")

    monkeypatch.setattr(purge_import, "load_settings", lambda: FakeSettings())
    monkeypatch.setattr(purge_import, "GlpiClient", lambda *a, **k: FakeGlpiCtx())
    monkeypatch.setattr(purge_import, "build_purge_plan", boom_plan)

    code = purge_import.main([])

    assert code == purge_import.EXIT_FAILED
    err = capsys.readouterr().err
    assert secret not in err
    assert messages.REDACTED in err


# -- --report must work in dry-run -----------------------------------------
#
# It did not: main() returned at the `if not args.apply` gate before ever
# reaching the write, so `purge_import.py --report x.txt` printed the plan and
# silently saved nothing. That file is exactly the evidence an operator reads
# BEFORE an irreversible purge, so a flag that quietly does nothing there is
# worse than no flag at all.

from purge_import import DEFAULT_PURGE_RECORD, save_report  # noqa: E402


def a_target():
    return [PurgeTarget(100, "Casca", 0, [7], "17343")]


def test_report_flag_saves_the_plan_during_a_dry_run(tmp_path):
    target = tmp_path / "plan.txt"

    written = save_report(str(target), a_target(), applied=False)

    assert written == target
    text = target.read_text(encoding="utf-8")
    assert "100" in text and "Casca" in text


def test_a_dry_run_plan_is_labelled_planned_not_executed(tmp_path):
    target = tmp_path / "plan.txt"

    save_report(str(target), a_target(), applied=False)

    text = target.read_text(encoding="utf-8")
    assert messages.PURGE_HEADER_PLANNED in text
    assert messages.PURGE_HEADER_APPLIED not in text


def test_a_dry_run_without_the_flag_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert save_report(None, a_target(), applied=False) is None
    assert not (tmp_path / DEFAULT_PURGE_RECORD).exists()


def test_a_real_run_without_the_flag_writes_the_default_record(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    written = save_report(None, a_target(), applied=True)

    assert written == Path(DEFAULT_PURGE_RECORD)
    assert messages.PURGE_HEADER_APPLIED in written.read_text(encoding="utf-8")


def test_report_flag_creates_a_missing_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "plan.txt"

    assert save_report(str(target), a_target(), applied=False) == target
    assert target.exists()

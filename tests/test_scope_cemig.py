"""CEMIG enters scope: tracker 39 as a root, tracker 40 as a task.

Opened 2026-08-19. Spec 11.1 kept tracker 39 out because three of container 15's
five mandatory columns have no source on it (`Valor do Projeto`, `Gestão`,
`Responsável Cliente` do not exist on that tracker at all) and GLPI would refuse
`POST /Project`. A live read the same day found `mandatory: 0` on all 25 fields
of container 15, so the refusal no longer happens.

Two asymmetries are deliberate and are what these tests pin:

  - tracker 39 is a ROOT tracker only. Measured live: all six tracker-39 issues
    have no parent, so it never appears as a child. Adding it to the task set
    would be a guess, and "the naive way to add CEMIG" is exactly to add both
    trackers to both sets.
  - tracker 40 gets its OWN ProjectTaskType, the way 18 (Atividade) and 41
    (Compras) did, rather than borrowing Atividade's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from clients.redmine import TreeNode  # noqa: E402
from config.settings import (  # noqa: E402
    IN_SCOPE_ROOT_TRACKERS,
    IN_SCOPE_TASK_TRACKERS,
    PROJECTTASKTYPE_SUBTAREFA_CEMIG,
    TRACKER_PROJETO_CEMIG,
    TRACKER_SUBTAREFA_CEMIG,
    load_yaml,
)
from resolve.status import StatusResolver  # noqa: E402
from transform.mapper import Mapper, Outcome  # noqa: E402
from transform.tree import Disposition, plan_tree  # noqa: E402

# RDM 19074 "Merit Triangulo" and one of its four subtasks, trimmed to the keys
# the tree walk and the task mapping actually read.
CEMIG_ROOT_ID = 19074
CEMIG_CHILD_ID = 19075


def issue(issue_id: int, tracker_id: int, tracker_name: str, **extra) -> dict:
    data = {
        "id": issue_id,
        "subject": f"RDM {issue_id}",
        "tracker": {"id": tracker_id, "name": tracker_name},
        "status": {"id": 15, "name": "Novo"},
        "custom_fields": [],
    }
    data.update(extra)
    return data


def cemig_tree() -> TreeNode:
    child = TreeNode(issue=issue(CEMIG_CHILD_ID, 40, "Subtarefa Cemig"))
    return TreeNode(issue=issue(CEMIG_ROOT_ID, 39, "Projeto CEMIG"), children=[child])


def task_mapper() -> Mapper:
    """A Mapper for task_core only - it needs no dropdown or user lookup."""
    return Mapper(
        mapping=load_yaml("mapping.yml"),
        status_resolver=StatusResolver(),
        user_resolver=None,
        dropdown_resolver=None,
    )


# -- the tree walk ---------------------------------------------------------


def test_subtarefa_cemig_child_becomes_a_task():
    """The whole point: 35 subtasks that used to be reported and left behind."""
    nodes = {item.issue_id: item for item in plan_tree(cemig_tree()).nodes}

    assert nodes[CEMIG_ROOT_ID].disposition is Disposition.PROJECT
    assert nodes[CEMIG_CHILD_ID].disposition is Disposition.TASK


def test_projeto_cemig_stays_out_of_the_task_trackers():
    """A tracker-39 issue found as a CHILD is still skipped.

    No such issue exists in Redmine today - which is the reason 39 is a root
    tracker only. This test fails the moment someone "completes" the change by
    adding 39 to both sets.
    """
    root = TreeNode(
        issue=issue(20238, 14, "Projeto"),
        children=[TreeNode(issue=issue(CEMIG_ROOT_ID, 39, "Projeto CEMIG"))],
    )

    nodes = {item.issue_id: item for item in plan_tree(root).nodes}

    assert nodes[CEMIG_ROOT_ID].disposition is Disposition.SKIPPED
    assert TRACKER_PROJETO_CEMIG in IN_SCOPE_ROOT_TRACKERS
    assert TRACKER_PROJETO_CEMIG not in IN_SCOPE_TASK_TRACKERS
    assert TRACKER_SUBTAREFA_CEMIG in IN_SCOPE_TASK_TRACKERS


# -- the task type ---------------------------------------------------------


def test_subtarefa_cemig_gets_its_own_task_type():
    result = task_mapper().map_task(issue(CEMIG_CHILD_ID, 40, "Subtarefa Cemig"))

    assert result.payload["projecttasktypes_id"] == PROJECTTASKTYPE_SUBTAREFA_CEMIG
    typed = [r for r in result.records if r.target_column == "projecttasktypes_id"]
    assert [r.outcome for r in typed] == [Outcome.WRITTEN]


def test_pendencia_fields_land_in_the_task_comment():
    """Tracker 40's only two custom fields have no column in glpi_projecttasks."""
    source = issue(
        CEMIG_CHILD_ID,
        40,
        "Subtarefa Cemig",
        custom_fields=[
            {"id": 1, "name": "Pendência", "value": "Cliente"},
            {"id": 2, "name": "Tipo de Pendência", "value": "Aguardando retorno"},
        ],
    )

    result = task_mapper().map_task(source)

    assert "Pendência: Cliente" in result.payload["comment"]
    assert "Tipo de Pendência: Aguardando retorno" in result.payload["comment"]
    dumped = [r for r in result.records if r.source_label.startswith("Pend")]
    assert [r.outcome for r in dumped] == [Outcome.NO_COUNTERPART]


# -- the root-tracker guard ------------------------------------------------


def test_a_cemig_root_is_accepted():
    assert main.root_tracker_rejection(issue(CEMIG_ROOT_ID, 39, "Projeto CEMIG")) is None


def test_a_root_outside_the_scope_is_refused():
    """Faturamento is a task tracker; starting a migration on one is a mistake."""
    rejection = main.root_tracker_rejection(issue(20389, 15, "Faturamento"))

    assert rejection is not None
    assert "15" in rejection and "Faturamento" in rejection


def test_a_task_tracker_is_not_a_root_by_accident():
    """18 and 41 are in IN_SCOPE_TASK_TRACKERS but must not start a migration."""
    assert main.root_tracker_rejection(issue(20154, 41, "Compras")) is not None
    assert main.root_tracker_rejection(issue(20156, 18, "Atividades")) is not None


def test_an_issue_with_no_tracker_is_refused_rather_than_crashing():
    assert main.root_tracker_rejection({"id": 1, "subject": "sem tracker"}) is not None

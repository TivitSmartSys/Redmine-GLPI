"""A Faturamento reachable by BOTH paths must be planned once, not twice.

Found by the audit of 2026-09-10. `build_project_plan` collects Faturamento
from two independent sources (spec 6.5): the tree walk, which classifies a
tracker-15 DESCENDANT, and the root's relations. Nothing compared the two, so a
tracker-15 issue that is both a descendant of the root and related to it was
appended to `plan.faturamento` twice - once with origin "child" and once with
origin "relation".

What that costs at apply time: step 4 creates the ProjectTask for the first
copy, the second copy finds it through `store.lookup` and reuses the id, and
then calls `create_faturamento_row` again - which has no update branch and no
dedup marker of its own, so the task ends up with TWO container rows. The
dry-run report lists the invoice twice as well.

The hazard was already known on the other side of the same function:
`plan_attachments` is deliberately passed only the relation-sourced issues,
"a tracker-15 descendant is already a node of tree_plan and would be counted
twice". The files were guarded; the Faturamento itself was not.

Measured on 150 Telecom roots the same day: not one of them has a tracker-15
child at all, so this is a latent defect rather than an active one. It is fixed
because the guard costs one parameter and the failure is silent, writes to
GLPI, and has no dedup marker that could catch it afterwards.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import TRACKER_FATURAMENTO  # noqa: E402
from transform.faturamento import discover_from_relations  # noqa: E402
from transform.tree import Disposition, plan_tree  # noqa: E402
from clients.redmine import TreeNode  # noqa: E402

FATURAMENTO = {
    "id": 900,
    "tracker": {"id": TRACKER_FATURAMENTO, "name": "Faturamento"},
    "subject": "NF 900",
}
OTHER_PROJECT = {"id": 800, "tracker": {"id": 14, "name": "Projeto"}, "subject": "P"}


def _root(*partner_ids: int) -> dict:
    return {
        "id": 100,
        "tracker": {"id": 14, "name": "Projeto"},
        "subject": "Raiz",
        "relations": [
            {"issue_id": 100, "issue_to_id": pid, "relation_type": "relates"}
            for pid in partner_ids
        ],
    }


class FakeRedmine:
    """Answers by id and records what it was actually asked for."""

    def __init__(self, *issues: dict):
        self._by_id = {int(issue["id"]): issue for issue in issues}
        self.fetched: list[int] = []

    def fetch_issue(self, issue_id: int, include=()):
        self.fetched.append(int(issue_id))
        return self._by_id[int(issue_id)]


def test_the_tree_and_the_relations_really_do_reach_the_same_issue():
    """The precondition. Without this the rest of the file proves nothing."""
    tree = plan_tree(TreeNode(issue=_root(900), children=[TreeNode(issue=FATURAMENTO, children=[])]))
    from_tree = [
        node.issue_id
        for node in tree.nodes
        if node.disposition is Disposition.FATURAMENTO
    ]

    discovery = discover_from_relations(FakeRedmine(FATURAMENTO), _root(900))

    assert from_tree == [900]
    assert [int(issue["id"]) for issue in discovery.issues] == [900]


def test_an_already_planned_faturamento_is_not_discovered_again():
    redmine = FakeRedmine(FATURAMENTO)

    discovery = discover_from_relations(redmine, _root(900), already_planned=(900,))

    assert discovery.issues == []


def test_an_already_planned_partner_is_never_even_fetched():
    """It costs a GET per root, and the answer is discarded either way."""
    redmine = FakeRedmine(FATURAMENTO)

    discover_from_relations(redmine, _root(900), already_planned=(900,))

    assert redmine.fetched == []


def test_an_already_planned_partner_is_not_reported_as_ignored():
    """It is migrated, by the other path. Listing it under "relações ignoradas"
    would tell the operator the opposite of what happened."""
    discovery = discover_from_relations(
        FakeRedmine(FATURAMENTO), _root(900), already_planned=(900,)
    )

    assert discovery.ignored_relations == []


def test_other_partners_are_unaffected_by_the_exclusion():
    redmine = FakeRedmine(FATURAMENTO, OTHER_PROJECT)

    discovery = discover_from_relations(
        redmine, _root(900, 800), already_planned=(900,)
    )

    assert redmine.fetched == [800]
    assert [item.issue_id for item in discovery.ignored_relations] == [800]


def test_the_default_still_discovers_everything():
    """No caller that omits the argument may change behaviour."""
    discovery = discover_from_relations(FakeRedmine(FATURAMENTO), _root(900))

    assert [int(issue["id"]) for issue in discovery.issues] == [900]

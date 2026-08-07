"""Decide what each node of the Redmine tree becomes in GLPI.

Model (spec section 3): the root is always a Project and every descendant is
normally a ProjectTask. The tracker drives exactly ONE branch - tracker 15
(Faturamento) becomes a ProjectTask of type Faturamento carrying a container-26
row, attached to the ROOT project rather than to its own parent - plus the scope
rule from section 1a.

Revised 2026-08-06: a Faturamento used to be a container-25 row on the Project,
which is why it is still classified separately from a plain TASK. Tracker 18
(Atividades) joined IN_SCOPE_TASK_TRACKERS in the same change and needs no
branch of its own - it is an ordinary task that happens to get a task type.
Tracker 41 (Compras) joined the same way later that day, once GLPI gained a
ProjectTaskType "Compras": membership in IN_SCOPE_TASK_TRACKERS is the whole
change, _classify below stays untouched.

Scope rule (closed decision, variant c): a child whose tracker is out of scope
is NOT created in GLPI; it goes to the report instead. Section 3 and Appendix D
still describe the older "tracker does not matter" behaviour; section 1a of v1.5
supersedes them, and the test list (20156, 18620, 18826) exists to exercise
exactly this rule.

Nothing disappears silently: the tree is walked in full even below a skipped
node, every skipped node gets its own report line, and a tracker-15 descendant
is still migrated because its task attaches to the ROOT project, not to the task
hierarchy it was found in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from clients.redmine import TreeNode
from config.settings import IN_SCOPE_TASK_TRACKERS, TRACKER_FATURAMENTO


class Disposition(str, Enum):
    PROJECT = "project"
    TASK = "task"
    FATURAMENTO = "faturamento"
    SKIPPED = "skipped"


# PT-BR reasons, rendered straight into the report.
REASON_OUT_OF_SCOPE = "tracker fora do escopo da migração"
REASON_ANCESTOR_SKIPPED = "descendente de uma subtarefa ignorada"


@dataclass
class PlannedNode:
    node: TreeNode
    disposition: Disposition
    depth: int
    parent_redmine_id: int | None = None
    reason: str = ""

    @property
    def issue_id(self) -> int:
        return self.node.issue_id

    @property
    def tracker_id(self) -> int | None:
        return self.node.tracker_id

    @property
    def tracker_name(self) -> str:
        return (self.node.issue.get("tracker") or {}).get("name") or ""

    @property
    def subject(self) -> str:
        return self.node.subject


@dataclass
class TreePlan:
    nodes: list[PlannedNode] = field(default_factory=list)

    def of(self, disposition: Disposition) -> list[PlannedNode]:
        return [item for item in self.nodes if item.disposition is disposition]

    @property
    def root(self) -> PlannedNode:
        return self.nodes[0]

    @property
    def tasks(self) -> list[PlannedNode]:
        return self.of(Disposition.TASK)

    @property
    def faturamento(self) -> list[PlannedNode]:
        return self.of(Disposition.FATURAMENTO)

    @property
    def skipped(self) -> list[PlannedNode]:
        return self.of(Disposition.SKIPPED)


def plan_tree(root: TreeNode) -> TreePlan:
    """Assign a disposition to every node, parent before child (pre-order)."""
    plan = TreePlan()
    _visit(root, plan, depth=0, parent_id=None, is_root=True, ancestor_skipped=False)
    return plan


def _visit(
    node: TreeNode,
    plan: TreePlan,
    depth: int,
    parent_id: int | None,
    is_root: bool,
    ancestor_skipped: bool,
) -> None:
    disposition, reason = _classify(node, is_root, ancestor_skipped)
    plan.nodes.append(
        PlannedNode(
            node=node,
            disposition=disposition,
            depth=depth,
            parent_redmine_id=parent_id,
            reason=reason,
        )
    )

    # A task can only hang off a task that was actually created. Faturamento is
    # exempt even though it is now a task itself: its task is created on the
    # ROOT project with no projecttasks_id, so a skipped ancestor cannot orphan
    # it (see apply_plan step 4).
    child_ancestor_skipped = ancestor_skipped or disposition is Disposition.SKIPPED
    for child in node.children:
        _visit(
            child,
            plan,
            depth=depth + 1,
            parent_id=node.issue_id,
            is_root=False,
            ancestor_skipped=child_ancestor_skipped,
        )


def _classify(
    node: TreeNode, is_root: bool, ancestor_skipped: bool
) -> tuple[Disposition, str]:
    if is_root:
        return Disposition.PROJECT, ""

    tracker_id = node.tracker_id

    # The single tracker-driven branch in the whole migration. Checked before
    # the scope rule so a Faturamento under a skipped parent is still captured -
    # it lands on the root project either way.
    if tracker_id == TRACKER_FATURAMENTO:
        return Disposition.FATURAMENTO, ""

    if ancestor_skipped:
        return Disposition.SKIPPED, REASON_ANCESTOR_SKIPPED

    if tracker_id in IN_SCOPE_TASK_TRACKERS:
        return Disposition.TASK, ""

    return Disposition.SKIPPED, REASON_OUT_OF_SCOPE

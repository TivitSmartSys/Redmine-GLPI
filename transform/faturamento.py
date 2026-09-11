"""Discover the Faturamento issues that belong to a migrated project (spec 6.5).

Container 25 is attached to Project, so every row needs an owning project. Two
paths are verified on real data and BOTH are mandatory:

  1. relations - the dominant case (20389 <-> 20172)
  2. parent    - 42 of 3409 issues (17306 -> parent 17081)

A related issue is migrated ONLY when its tracker is 15. Anything else is
reported and never migrated: `relates` also links Projeto<->Projeto (17582->18471,
18620->18655), so following relations blindly would turn projects into invoices.

v1 scope: only Faturamento linked to the migrated project. Unlinked Faturamento -
probably most of the 3409 - stays out of scope; a bulk migration would need its
own mode and its own decision about which project to attach rows to.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from clients.errors import RedmineError
from clients.redmine import relation_partner_id
from config.settings import TRACKER_FATURAMENTO


@dataclass
class RelatedIssue:
    issue_id: int
    tracker_id: int | None
    tracker_name: str
    subject: str
    relation_type: str = ""


@dataclass
class FaturamentoDiscovery:
    """Tracker-15 issues to migrate, plus everything deliberately left out."""

    issues: list[dict] = field(default_factory=list)
    ignored_relations: list[RelatedIssue] = field(default_factory=list)
    failures: list[tuple[int, str]] = field(default_factory=list)


def discover_from_relations(
    redmine, root_issue: dict, already_planned: Iterable[int] = ()
) -> FaturamentoDiscovery:
    """Walk the root issue's relations and keep only tracker-15 partners.

    `already_planned` holds the issue ids the TREE walk has already classified
    as Faturamento. The two paths of spec 6.5 are independent and can reach the
    same issue - a tracker-15 descendant of the root that is ALSO related to it
    - and until 2026-09-10 nothing compared them, so such an issue was planned
    twice. At apply time the second copy reuses the first copy's ProjectTask
    through the local map and then writes a SECOND container row on it;
    `create_faturamento_row` has no update branch and container 18 has no dedup
    marker, so nothing downstream could catch it.

    Seeding `seen` is the whole implementation: the partner is then never
    fetched (one GET saved per root) and cannot reach `ignored_relations`
    either, which is correct - it is migrated, by the other path, and listing
    it as ignored would tell the operator the opposite of what happened.

    The sibling guard has been in `main.build_project_plan` since attachments
    landed: `plan_attachments` is passed only the relation-sourced issues,
    because a tracker-15 descendant "is already a node of tree_plan and would
    be counted twice". The files were guarded; the invoice itself was not.
    """
    discovery = FaturamentoDiscovery()
    root_id = int(root_issue["id"])
    seen: set[int] = {int(issue_id) for issue_id in already_planned}

    for relation in root_issue.get("relations") or []:
        partner_id = relation_partner_id(relation, root_id)
        if partner_id is None or partner_id in seen:
            continue
        seen.add(partner_id)

        try:
            # `attachments` since 2026-08-10: a Faturamento reached by relation
            # becomes a ProjectTask that hosts its own files, and with the empty
            # include it arrived here without the key at all. `journals` since
            # 2026-08-11 for the same reason - that task hosts its own notes too.
            partner = redmine.fetch_issue(
                partner_id, include=("attachments", "journals")
            )
        except RedmineError as exc:
            # A relation we could not resolve still reaches the report.
            discovery.failures.append((partner_id, str(exc)))
            continue

        tracker = partner.get("tracker") or {}
        if tracker.get("id") == TRACKER_FATURAMENTO:
            discovery.issues.append(partner)
        else:
            discovery.ignored_relations.append(
                RelatedIssue(
                    issue_id=partner_id,
                    tracker_id=tracker.get("id"),
                    tracker_name=tracker.get("name") or "",
                    subject=partner.get("subject") or "",
                    relation_type=relation.get("relation_type") or "",
                )
            )

    return discovery

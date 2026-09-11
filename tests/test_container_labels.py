"""The operator must be told the container this instance actually writes to.

Found by the audit of 2026-09-10, in a live dry-run against production. The
report said

    Campos adicionais (container 15):
    Faturamentos (tarefa tipo Faturamento + container 26): 1

while the run was writing containers 17 and 18. The numbers were transcribed
when the tool was built against the TEST instance and never moved with the
production refit of 2026-09-03, which changed the ids but not the text.

The distinction is not pedantic here. Container 15 EXISTS on production and is
somebody else's: "adicionaltarefamp", a dom container on ProjectTask. Container
26 does not exist at all. An operator following the report into the GLPI
interface would open an unrelated container and find none of the values the
report promised.

These tests pin the labels to the constants rather than to the numbers, so the
text cannot go stale again the next time an instance numbers them differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from config.settings import (  # noqa: E402
    CONTAINER_ID_ADDITIONAL_FIELDS,
    CONTAINER_ID_FATURAMENTO,
)
from report import messages  # noqa: E402

PROJECT_LABELS = (
    "REPORT_CONTAINER15_HEADER",
    "APPLY_CONTAINER15_WRITTEN",
    "RESET_MARKER_HEADER",
)

FATURAMENTO_LABELS = (
    "REPORT_FATURAMENTO_LINE",
    "REPORT_SECTION_FATURAMENTO",
    "REPORT_TREE_FATURAMENTO",
    "REPORT_FATURAMENTO_ROW_PAYLOAD",
    "APPLY_FATURAMENTO_CREATED",
    "APPLY_FATURAMENTO_DEGRADED",
    "PREFLIGHT_PROJECTTASK_RIGHT_MISSING",
)


@pytest.mark.parametrize("name", PROJECT_LABELS)
def test_project_container_labels_name_the_real_container(name):
    text = getattr(messages, name)

    assert f"container {CONTAINER_ID_ADDITIONAL_FIELDS}" in text


@pytest.mark.parametrize("name", FATURAMENTO_LABELS)
def test_faturamento_container_labels_name_the_real_container(name):
    text = getattr(messages, name)

    assert f"container {CONTAINER_ID_FATURAMENTO}" in text


def test_no_user_facing_string_still_carries_a_hard_coded_container_number():
    """The whole module, not just the names listed above.

    A transcribed number is exactly the kind of thing that comes back in the
    next string somebody adds, so the guard is a sweep rather than a list.
    """
    stale = [
        name
        for name, value in vars(messages).items()
        if name.isupper()
        and isinstance(value, str)
        and ("container 15" in value or "container 26" in value)
    ]

    assert stale == []

"""purge_import.py is retired and must never run against this instance.

Closed decision 2026-09-10, taken by the manager after the audit of the same
day measured what the tool would do here. It was calibrated entirely against
the TEST instance, where 1253 of 1274 projects came from a bulk import that had
written `rdmfield` misaligned. Production is not that instance:

  * a dry-run selected 1004 projects, and NOT ONE of them carries an rdmfield
    marker - so there is no poisoned dedup for phase 0 to repair;
  * 236 of those 1004 are not even named "RDM <n>" ("Manutenção Preventiva ENEL
    RJ - SUBESTAÇÕES - Ciclo 2024/2025"), i.e. they are ordinary customer
    projects that predate this tool;
  * KEEP_PROJECT_IDS holds ids 1286-1298, which do not exist here at all - the
    highest project id is 1005 - so the "Projetos preservados: 9" line was
    reporting protection it could not provide, and the keep-list guard in
    select_targets can never fire.

Deletion uses force_purge, which skips the trash. The refusal therefore lives
in main() rather than in the docs: an instruction not to run something is not a
control, and CLAUDE.md used to present this as step one of the batch workflow.

The planning functions below main() are deliberately left intact and still
tested - they are the record of what was measured - but nothing can reach the
network through them any more.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import purge_import  # noqa: E402
from report import messages  # noqa: E402


class _ExplodingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("purge_import must never open a GLPI session")


def test_main_refuses_and_never_opens_a_session(monkeypatch):
    monkeypatch.setattr(purge_import, "GlpiClient", _ExplodingClient)

    assert purge_import.main([]) == purge_import.EXIT_CONFIG


def test_apply_and_yes_are_refused_just_the_same(monkeypatch):
    """The flag combination that would have purged 1004 projects unattended."""
    monkeypatch.setattr(purge_import, "GlpiClient", _ExplodingClient)

    assert purge_import.main(["--apply", "--yes"]) == purge_import.EXIT_CONFIG


def test_the_refusal_says_why_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(purge_import, "GlpiClient", _ExplodingClient)

    purge_import.main([])

    assert messages.PURGE_DISABLED in capsys.readouterr().err


def test_the_refusal_needs_no_configuration(monkeypatch):
    """It must refuse before load_settings, so a missing .env cannot mask it."""
    monkeypatch.setattr(purge_import, "GlpiClient", _ExplodingClient)

    def _no_settings():
        raise AssertionError("purge_import must refuse before reading settings")

    monkeypatch.setattr(purge_import, "load_settings", _no_settings)

    assert purge_import.main([]) == purge_import.EXIT_CONFIG

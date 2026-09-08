"""The Redmine API is read-only. This test exists so that stays true.

Closed decision 2026-08-27: the migration reads Redmine and never writes to it.
Migration state lives in GLPI (the rdmfield marker) and in the local ledger.
The batch phase is where this is most likely to be broken by accident - looping
over thousands of issues makes "mark it as migrated in Redmine" feel natural.
It is not allowed.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clients.redmine as redmine_module  # noqa: E402

WRITE_VERBS = ("post", "put", "delete", "patch")


def test_redmine_client_source_has_no_write_verb():
    source = inspect.getsource(redmine_module)
    for verb in WRITE_VERBS:
        pattern = rf"\.{verb}\s*\("
        assert not re.search(pattern, source), (
            f"clients/redmine.py calls .{verb}() - the Redmine API is read-only"
        )


def test_redmine_client_exposes_no_write_method():
    from clients.redmine import RedmineClient

    for name in dir(RedmineClient):
        assert not name.lower().startswith(WRITE_VERBS), (
            f"RedmineClient.{name} looks like a write method; Redmine is read-only"
        )

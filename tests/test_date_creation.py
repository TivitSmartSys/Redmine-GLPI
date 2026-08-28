"""Redmine's created_on becomes GLPI's date_creation, on the project and on
every task.

Where the date lives: `created_on` is a plain attribute of the issue, not a
custom field. It arrives with every issue the plan reads - the root, every
child expanded by `_expand`, and every relation-sourced Faturamento - so unlike
`journals` and `attachments` it needed no change to any `include` list.

Two facts measured live on 2026-08-27 shape what is pinned here:

  1. `POST /Project` and `POST /ProjectTask` both HONOUR a `date_creation` sent
     in the input - GLPI does not overwrite it with the server clock. Verified
     on throwaway project 1297 and task 14215 (created with 2011-03-04
     09:08:07, read back unchanged, purged). No PUT, no repair step.
  2. The GLPI server's own clock runs at UTC-3: it stamped `date_mod`
     `2026-08-27 10:35:24` on that probe while UTC read 13:35. Redmine's REST
     API answers in UTC with a `Z` suffix. Copying the source string verbatim
     would therefore file every project three hours ahead of GLPI's own
     timestamps - and a day late for anything created after 21:00 local time.
     Hence the shift, and hence `datetime` being its own transform: `date`
     deliberately cuts the clock off and must not learn about timezones.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_yaml  # noqa: E402
from transform.mapper import Mapper, Outcome  # noqa: E402

MAPPING = load_yaml("mapping.yml")


def issue(created_on, tracker_id=14, **extra):
    data = {
        "id": 20438,
        "subject": "GPG | Projeto",
        "tracker": {"id": tracker_id, "name": "Projeto"},
        "created_on": created_on,
    }
    data.update(extra)
    return data


def mapper():
    return Mapper(mapping=MAPPING, status_resolver=None, user_resolver=None,
                  dropdown_resolver=None)


def record_for(result, column):
    return next(r for r in result.records if r.target_column == column)


# -- the conversion itself -------------------------------------------------

def test_utc_is_shifted_to_the_glpi_server_clock():
    """15:59:29Z is 12:59:29 on a UTC-3 server."""
    core, _c15 = mapper().map_project(issue("2016-03-30T15:59:29Z"))
    assert core.payload["date_creation"] == "2016-03-30 12:59:29"


def test_the_shift_can_move_the_day_backwards():
    """00:30Z on the 21st is 21:30 on the 20th - the case verbatim would lose."""
    core, _c15 = mapper().map_project(issue("2016-12-21T00:30:00Z"))
    assert core.payload["date_creation"] == "2016-12-20 21:30:00"


def test_a_value_already_in_glpi_shape_is_kept_as_is():
    """No Z, no offset: nothing to convert, so nothing is invented."""
    core, _c15 = mapper().map_project(issue("2016-03-30 15:59:29"))
    assert core.payload["date_creation"] == "2016-03-30 15:59:29"


# -- the mapping is wired on both sections ---------------------------------

def test_the_project_gets_date_creation():
    core, _c15 = mapper().map_project(issue("2026-07-31T19:21:57Z"))
    assert core.payload["date_creation"] == "2026-07-31 16:21:57"
    assert record_for(core, "date_creation").outcome is Outcome.WRITTEN


def test_a_task_gets_its_own_date_creation():
    """Not the project's - each task carries the date of ITS issue."""
    result = mapper().map_task(issue("2026-08-01T09:00:00Z", tracker_id=18))
    assert result.payload["date_creation"] == "2026-08-01 06:00:00"


def test_a_faturamento_gets_it_too():
    """Faturamento maps through task_core like every other task."""
    core, _c26 = mapper().map_faturamento(issue("2026-08-02T23:10:00Z", tracker_id=15))
    assert core.payload["date_creation"] == "2026-08-02 20:10:00"


# -- nothing is invented, nothing disappears -------------------------------

def test_a_missing_created_on_writes_no_key():
    core, _c15 = mapper().map_project(issue(None))
    assert "date_creation" not in core.payload
    assert record_for(core, "date_creation").outcome is Outcome.EMPTY_SOURCE


def test_an_unparsable_value_is_skipped_and_reported():
    """Rule 2 shape: no match -> skip the field and say so, never guess."""
    core, _c15 = mapper().map_project(issue("ontem de manhã"))
    assert "date_creation" not in core.payload
    record = record_for(core, "date_creation")
    assert record.outcome is Outcome.UNRESOLVED
    assert "ontem de manhã" in record.detail

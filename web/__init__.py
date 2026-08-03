"""Local web panel over the existing migration code.

The package adds a second front end; it does not add a second implementation.
Every migration step is executed by the very functions main.py calls, in the
same order and inside the same API sessions.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Same idiom as main.py / audit_coverage.py: run from source without installing.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

"""Configuration loading and migration-wide constants.

Secrets come exclusively from environment variables / .env (spec section 2).
Nothing here may ever be printed - see report.messages.redact().
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONFIG_DIR.parent

# ---------------------------------------------------------------------------
# GLPI plugin containers (verified via API, spec sections 6.1 and 6.5)
# ---------------------------------------------------------------------------

# Container 15 - "camposadicionaisprojetos", type "dom", one row per Project.
CONTAINER_ID_ADDITIONAL_FIELDS = 15
ITEMTYPE_ADDITIONAL_FIELDS = "PluginFieldsProjectcamposadicionaisprojeto"

# Container 25 - "faturamento", type "tab", multiple rows per Project.
# Container 26 is the twin for another itemtype and must NOT be used here.
CONTAINER_ID_FATURAMENTO = 25
ITEMTYPE_FATURAMENTO = "PluginFieldsProjectfaturamento"

# ---------------------------------------------------------------------------
# Tracker scope (spec section 1a + overriding rules from PROMPT_dla_Claude_Code)
# ---------------------------------------------------------------------------

# Redmine tracker that becomes a container-25 row instead of a ProjectTask.
# This is the ONLY tracker that drives branching logic (spec section 3).
TRACKER_FATURAMENTO = 15

# Trackers allowed to become a GLPI ProjectTask when found as a descendant.
# Everything else falls under the "child out of scope -> skip + report" rule
# (spec section 1a, decision variant c, closed in v1.5).
#
# Deliberately absent:
#   39 Projeto CEMIG    - entirely out of scope
#   40 Subtarefa Cemig  - child of CEMIG projects only, out of scope
#   41 Compras          - out of scope as a separate entity
#   18 Atividades       - phase two
IN_SCOPE_TASK_TRACKERS = frozenset({14, 42})

# Trackers accepted as a migration root.
IN_SCOPE_ROOT_TRACKERS = frozenset({14, 42})

# ---------------------------------------------------------------------------
# Container 15 fields flagged mandatory in GLPI (spec section 11.1).
# Missing data here may cause GLPI to reject the row - the dry-run report must
# highlight them before we attempt any write.
# ---------------------------------------------------------------------------
MANDATORY_CONTAINER15_COLUMNS = (
    "valordoprojetofield",
    "responsvelclientefieldtwo",
    "plugin_fields_gestofielddropdowns_id",
    "plugin_fields_despesafielddropdowns_id",
    "plugin_fields_complexidadefielddropdowns_id",
)

# ---------------------------------------------------------------------------
# entities_id - spec 11.3, RESOLVED by observation on 2026-07-30.
#
# We deliberately do not send entities_id. Verified on the first real write
# (RDM 20238 -> Project 1265): GLPI placed the project in entity 75,
# "TIVIT > SMART SYSTEMS", i.e. the API session's active entity, and the
# container-15 row inherited the same entity. Set this constant and send the
# key explicitly only if projects must land somewhere else.
# ---------------------------------------------------------------------------
OBSERVED_SESSION_ENTITY_ID = 75  # informational; not sent in any payload

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = PROJECT_ROOT / "migration.db"
HTTP_TIMEOUT_SECONDS = 60

# Range used when pulling a whole dropdown dictionary in one call (spec 6.3).
DROPDOWN_FETCH_RANGE = "0-999"


class ConfigError(Exception):
    """Raised when required configuration is missing or unreadable."""


@dataclass(frozen=True)
class Settings:
    """Runtime credentials and endpoints.

    Never log or repr() this object - it carries API tokens.
    """

    redmine_url: str
    redmine_api_key: str
    glpi_url: str
    glpi_user_token: str
    glpi_app_token: str

    def __repr__(self) -> str:  # pragma: no cover - safety net against leaks
        return "Settings(<redacted>)"

    __str__ = __repr__

    def secret_values(self) -> tuple[str, ...]:
        """Every value that must be scrubbed from any user-facing output."""
        return tuple(
            value
            for value in (self.redmine_api_key, self.glpi_user_token, self.glpi_app_token)
            if value
        )


REQUIRED_ENV_VARS = (
    "REDMINE_URL",
    "REDMINE_API_KEY",
    "GLPI_URL",
    "GLPI_USER_TOKEN",
    "GLPI_APP_TOKEN",
)


def load_settings(env_file: Path | None = None) -> Settings:
    """Read credentials from .env / environment.

    Raises ConfigError listing the missing variable names (names only - never
    values) so the CLI can render a PT-BR message.
    """
    load_dotenv(dotenv_path=env_file or PROJECT_ROOT / ".env", override=False)

    values = {name: (os.environ.get(name) or "").strip() for name in REQUIRED_ENV_VARS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(", ".join(missing))

    redmine_url = values["REDMINE_URL"].rstrip("/")
    glpi_url = values["GLPI_URL"].rstrip("/")

    # Redmine and GLPI are two different servers; apirest.php belongs only to
    # GLPI. Catch the classic copy/paste mistake early (spec section 2).
    if "apirest.php" in redmine_url:
        raise ConfigError("REDMINE_URL")

    return Settings(
        redmine_url=redmine_url,
        redmine_api_key=values["REDMINE_API_KEY"],
        glpi_url=glpi_url,
        glpi_user_token=values["GLPI_USER_TOKEN"],
        glpi_app_token=values["GLPI_APP_TOKEN"],
    )


def load_yaml(filename: str) -> dict:
    """Load one of the YAML config files from config/."""
    path = CONFIG_DIR / filename
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(str(path)) from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(str(path))
    return data

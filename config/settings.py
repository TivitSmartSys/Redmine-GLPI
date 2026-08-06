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

# Container 26 - "faturamento", type "tab", attached to ProjectTask.
#
# CHANGED 2026-08-06. Until now Faturamento was a container-25 row on the
# Project ("container 26 is the twin for another itemtype and must NOT be used
# here"). GLPI was reconfigured: a tracker-15 issue is now a ProjectTask of type
# Faturamento carrying a container-26 row, and container 25 is abandoned - it is
# still is_active 1 on the instance, we simply never write to it again.
# Container 26 uses its own column spelling (the "...fieldtwo" suffix), so the
# two containers are NOT interchangeable; see the container26 section of
# mapping.yml.
CONTAINER_ID_FATURAMENTO = 26
ITEMTYPE_FATURAMENTO = "PluginFieldsProjecttaskfaturamento"

# Container 16 - "camposadicionaistarefasdeprojeto", type "dom", on ProjectTask.
# Deliberately NOT written in this version (closed decision 2026-08-06): of its
# five fields three are is_active 0, and the dictionary behind the one real
# counterpart (tipodesitefield <- "Tipo de Site") is empty, so every value would
# be skipped anyway. Recorded here because it is a "dom" container: the moment
# any of its fields is flagged mandatory, POST /ProjectTask starts failing the
# same way POST /Project did, and the fix is the one in main.project_create_payload.
CONTAINER_ID_TASK_ADDITIONAL_FIELDS = 16

# ---------------------------------------------------------------------------
# Tracker scope (spec section 1a + overriding rules from PROMPT_dla_Claude_Code)
# ---------------------------------------------------------------------------

# Redmine tracker that becomes a ProjectTask of type Faturamento carrying a
# container-26 row, instead of a plain ProjectTask. This is the ONLY tracker
# that drives branching logic (spec section 3).
TRACKER_FATURAMENTO = 15

# Redmine tracker 18 "Atividades" - migrated as a ProjectTask of type Atividade.
TRACKER_ATIVIDADES = 18

# Trackers allowed to become a GLPI ProjectTask when found as a descendant.
# Everything else falls under the "child out of scope -> skip + report" rule
# (spec section 1a, decision variant c, closed in v1.5).
#
# Deliberately absent:
#   39 Projeto CEMIG    - entirely out of scope
#   40 Subtarefa Cemig  - child of CEMIG projects only, out of scope
#   41 Compras          - out of scope as a separate entity
#
# 18 Atividades moved OUT of phase two on 2026-08-06: GLPI now has a
# ProjectTaskType "Atividade", so the 1259 issues become typed tasks.
IN_SCOPE_TASK_TRACKERS = frozenset({14, 42, 18})

# Trackers accepted as a migration root. Atividades is a task-only tracker -
# it is never a root, only a descendant.
IN_SCOPE_ROOT_TRACKERS = frozenset({14, 42})

# ---------------------------------------------------------------------------
# glpi_projecttasktypes - verified live 2026-08-06.
# Only trackers listed here get a type; 14 and 42 deliberately get none, so an
# untyped task reads as intentional rather than as a failed lookup.
# ---------------------------------------------------------------------------
PROJECTTASKTYPE_FATURAMENTO = 3
PROJECTTASKTYPE_ATIVIDADE = 4

TRACKER_TO_PROJECTTASKTYPE = {
    TRACKER_FATURAMENTO: PROJECTTASKTYPE_FATURAMENTO,
    TRACKER_ATIVIDADES: PROJECTTASKTYPE_ATIVIDADE,
}

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
# Container 26 fields flagged mandatory in GLPI (verified live 2026-08-06).
#
# These behave DIFFERENTLY from the container-15 ones above, and the report must
# not conflate the two. Container 15 is type "dom": its values travel inside the
# POST /Project input, where the plugin's pre_item_add hook validates them, so a
# missing value REJECTS the project (that is the ERROR_GLPI_ADD failure of
# 2026-08-04). Container 26 is type "tab": we write its row straight to the
# container itemtype over REST, a path that never reaches validateValues() -
# verified in plugin source 1.24.3, where the generated row class extends
# PluginFieldsAbstractContainerInstance and neither overrides prepareInputForAdd
# nor calls validateValues(). A missing value here leaves the row incomplete;
# it does not refuse the write. GLPI's own UI will refuse to save the tab by
# hand until someone fills it.
# ---------------------------------------------------------------------------
# NOTE the two spellings. A Fields-plugin column keeps the field's own name for
# every type EXCEPT dropdown, where the column is
# `plugin_fields_<name>dropdowns_id` - confirmed 2026-08-06 against the live
# container-25 row, which stores `plugin_fields_prioridadefielddropdowns_id`
# next to plain `ttulofield`. The GLPI field is named `statusfaturamentofield`;
# its COLUMN is the long form below.
#
# plugin_fields_statusfaturamentofielddropdowns_id is listed here but
# deliberately never written (see never_write in mapping.yml): the task's core
# Estado already carries that status. It stays in this tuple ON PURPOSE so
# report section 5 keeps showing the open GLPI-side request to unflag it.
# Remove the entry once an admin has done so.
MANDATORY_CONTAINER26_COLUMNS = (
    "ttulofieldtwo",
    "plugin_fields_statusfaturamentofielddropdowns_id",
    "valortotaldanffieldtwo",
    "responsvelclientenffield",
    "plugin_fields_prioridadefielddropdowns_id",
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

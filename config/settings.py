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
# GLPI profile rights - the bit values are GLPI core constants (inc/define.php:
# READ 1, UPDATE 2, CREATE 4, DELETE 8, PURGE 16).
#
# Why we look at `projecttask` specifically. Diagnosed live on 2026-08-07 after
# POST /PluginFieldsProjecttaskfaturamento answered
#   ['ERROR_GLPI_ADD', 'Você não tem permissão para executar essa ação.']
# on RDM 18547 / task 14095. The Fields plugin checks the HOST itemtype's plain
# UPDATE bit before inserting a container row, so a "tab" container attached to
# a ProjectTask needs UPDATE on `projecttask`. The API profile (Super-Admin,
# id 4) had project=1151 but projecttask=1145 - the two missing bits being
# exactly UPDATE(2) and CREATE(4).
#
# Isolated by elimination, all four verified against the live instance:
#   - a minimal payload with no data column at all is refused  -> not the values
#   - the same payload plus entities_id=75 is refused           -> not the entity
#   - the same shape on container 25 (host Project) succeeds    -> not the code
#   - PUT /ProjectTask/<id> succeeds                            -> GLPI honours
#     "update my own tasks" there, while the plugin wants the plain UPDATE bit,
#     which is why the symptoms look contradictory
#
# The consequence is limited: only the container-26 row is refused, the task
# itself is created and apply degrades with a warning. Hence preflight WARNS
# rather than aborting - a project with no Faturamento is unaffected.
GLPI_RIGHT_UPDATE = 2
GLPI_RIGHTNAME_PROJECTTASK = "projecttask"

# ---------------------------------------------------------------------------
# Tracker scope (spec section 1a + overriding rules from PROMPT_dla_Claude_Code)
# ---------------------------------------------------------------------------

# Redmine tracker that becomes a ProjectTask of type Faturamento carrying a
# container-26 row, instead of a plain ProjectTask. This is the ONLY tracker
# that drives branching logic (spec section 3).
TRACKER_FATURAMENTO = 15

# Redmine tracker 18 "Atividades" - migrated as a ProjectTask of type Atividade.
TRACKER_ATIVIDADES = 18

# Redmine tracker 41 "Compras" - migrated as a ProjectTask of type Compras.
TRACKER_COMPRAS = 41

# Trackers allowed to become a GLPI ProjectTask when found as a descendant.
# Everything else falls under the "child out of scope -> skip + report" rule
# (spec section 1a, decision variant c, closed in v1.5).
#
# Deliberately absent:
#   39 Projeto CEMIG    - entirely out of scope
#   40 Subtarefa Cemig  - child of CEMIG projects only, out of scope
#
# 18 Atividades moved OUT of phase two on 2026-08-06: GLPI now has a
# ProjectTaskType "Atividade", so the 1259 issues become typed tasks.
#
# 41 Compras joined on 2026-08-06 for the same reason - a ProjectTaskType
# "Compras" (id 5) was created in GLPI. Measured live the same day: 72 issues,
# of which 56 hang directly off a tracker-14 (53) or tracker-42 (3) root and are
# therefore reachable by the tree walk. No Compras sits under an Atividade or
# under a skipped node, and none has children of its own, so the change adds no
# cascade. The remaining 16 have no parent at all: nothing reaches them and they
# are knowingly left unmigrated - Compras is a task tracker, never a root (its
# seven custom fields do not fit container 15 and the five mandatory columns
# would be empty, which GLPI would reject).
IN_SCOPE_TASK_TRACKERS = frozenset({14, 42, 18, 41})

# Trackers accepted as a migration root. Atividades is a task-only tracker -
# it is never a root, only a descendant.
IN_SCOPE_ROOT_TRACKERS = frozenset({14, 42})

# ---------------------------------------------------------------------------
# glpi_projecttasktypes - verified live 2026-08-06, the full table being
#   1 Deslocamento, 2 Manutenção Preventiva, 3 Faturamento, 4 Atividade,
#   5 Compras
# Only trackers listed here get a type; 14 and 42 deliberately get none, so an
# untyped task reads as intentional rather than as a failed lookup.
# ---------------------------------------------------------------------------
PROJECTTASKTYPE_FATURAMENTO = 3
PROJECTTASKTYPE_ATIVIDADE = 4
PROJECTTASKTYPE_COMPRAS = 5

TRACKER_TO_PROJECTTASKTYPE = {
    TRACKER_FATURAMENTO: PROJECTTASKTYPE_FATURAMENTO,
    TRACKER_ATIVIDADES: PROJECTTASKTYPE_ATIVIDADE,
    TRACKER_COMPRAS: PROJECTTASKTYPE_COMPRAS,
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
# Container 26 fields flagged mandatory in GLPI - EMPTY since 2026-08-07.
#
# It used to hold five columns, one of them
# plugin_fields_statusfaturamentofielddropdowns_id, kept in the tuple ON PURPOSE
# so report section 5 would keep showing the open request to unflag field 225.
# That request has been granted: a sweep of GET /PluginFieldsField on 2026-08-07
# found mandatory=0 on EVERY field of container 26 - and of container 15 too.
# The tuple is emptied rather than deleted because both reporter.py and
# web/summary.py iterate it; an empty tuple simply stops producing the block.
#
# Container 26 flags never refused a write anyway (it is type "tab": the row is
# written straight to the container itemtype, a path that never reaches the
# plugin's validateValues() - verified in plugin source 1.24.3, where the
# generated row class extends PluginFieldsAbstractContainerInstance and neither
# overrides prepareInputForAdd nor calls validateValues). They only drove the
# report. Container 15 is the one where a mandatory flag REJECTS the project,
# which is why MANDATORY_CONTAINER15_COLUMNS above is left populated even though
# the live flags are currently 0 - it is a cheap guard against an admin
# re-flagging them, and the instance has flipped these before.
#
# NOTE for whoever repopulates this: a Fields-plugin column keeps the field's
# own name for every type EXCEPT dropdown, where the column is
# `plugin_fields_<name>dropdowns_id` - confirmed 2026-08-06 against the live
# container-25 row, which stores `plugin_fields_prioridadefielddropdowns_id`
# next to plain `ttulofield`.
MANDATORY_CONTAINER26_COLUMNS: tuple[str, ...] = ()

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
# Attachments -> GLPI Documents (spec section 3 left these out of v1; opened
# 2026-08-10).
#
# A Redmine attachment becomes a Document linked through Document_Item to the
# item its issue became. Verified on the live instance 2026-08-10, GLPI 11.0.6:
#   - CFG_GLPI['document_types'] lists both Project and ProjectTask, so both
#     hosts carry a "Documentos" tab;
#   - the API profile has document = 255 (every bit);
#   - glpi_documenttypes holds 76 rows, .xlsb/.msg/.eml/.ods among them, all
#     is_uploadable = 1 - which matters because RDM 16467 uses all four.
# ---------------------------------------------------------------------------
ITEMTYPE_DOCUMENT = "Document"
ITEMTYPE_DOCUMENT_ITEM = "Document_Item"

# ---------------------------------------------------------------------------
# Notes -> GLPI Notepad (the "Notas" tab)
#
# A Redmine journal entry that carries text becomes a Notepad row on the item
# its issue became. Verified on the live instance 2026-08-11, GLPI 11.0.6:
#   - GET /Project/1277/Notepad and GET /ProjectTask/14107/Notepad both answer
#     with a list, so both hosts carry the tab;
#   - GET /Notepad returns rows with itemtype Project and ProjectTask alike,
#     i.e. neither host is theoretical;
#   - GET /getActiveProfile has NO `notepad` key - a Notepad row rides on the
#     host item's right, and the profile already has project = 1151 and
#     projecttask = 1151. No new grant is needed, unlike container 26.
# ---------------------------------------------------------------------------
ITEMTYPE_NOTEPAD = "Notepad"

# ---------------------------------------------------------------------------
# Plugin "text" columns are VARCHAR(255)
#
# Diagnosed live 2026-08-11 on RDM 17444: POST /Project came back with
#   ERROR_GLPI_ADD "MySQL query error: Data too long for column
#   'andamentodoprojetofield' at row 1 (1406)"
# because "Andamento do Projeto" held 480 characters. Every Fields-plugin field
# of type `text` (verified via GET /PluginFieldsField: fields 160, 162, 186 and
# 189 of container 15) lands in a VARCHAR(255); the plugin's `textarea` type is
# the one backed by TEXT.
#
# The failure mode is what makes this dangerous rather than merely annoying:
# GLPI COMMITS THE PROJECT and only then does the plugin's add hook fail on its
# own INSERT. The project survives without a container row, so it carries no
# `rdmfield` marker, dedup cannot see it, and the next run creates a duplicate.
# That is the opposite of the mandatory-field case, where the plugin's
# validation refuses the project itself before anything is written.
#
# A sweep of trackers 14 and 42 on 2026-08-11 found 92 of 5589 issues over the
# limit, the worst at 2788 characters (RDM 2313).
#
# Closed decision 2026-08-11: truncate and report. This is a deliberate, narrow
# exception to "copy migrated data verbatim" - the alternative was dropping the
# field entirely on those 92 projects. Every truncation gets its own report line
# carrying the original length, so the loss is never silent.
PLUGIN_TEXT_MAX_LENGTH = 255

# Mapping sections whose columns live in a plugin container, i.e. the ones the
# limit above applies to. Core GLPI columns (Project.content, Project.comment,
# ProjectTask.comment) are TEXT and must NOT be truncated.
PLUGIN_CONTAINER_SECTIONS = frozenset({"container15", "container26"})

# GLPI right name for documents, read from GET /getActiveProfile at preflight.
GLPI_RIGHTNAME_DOCUMENT = "document"
GLPI_RIGHT_CREATE = 4

# CFG_GLPI['document_max_size'] read live 2026-08-10: 50 (MB). Kept as a
# constant rather than read per run - a file over the limit is skipped and
# reported before anything is downloaded, and the value has to be known during
# the dry-run, which never opens a GLPI config call for it.
DOCUMENT_MAX_SIZE_MB = 50
DOCUMENT_MAX_SIZE_BYTES = DOCUMENT_MAX_SIZE_MB * 1024 * 1024

# Deduplication marker for documents, written into Document.comment.
#
# The same reasoning as `rdmfield` on container 15 (spec 9.1): Redmine and GLPI
# ids are independent, so the marker - not the id - is what proves a file was
# already migrated. GLPI is the authority; the SQLite map is a cache.
# Containers 16 and 26 have no marker field at all, which is why tasks rely on
# the local map alone; documents deliberately do not repeat that weakness.
DOCUMENT_MARKER_PREFIX = "rdmattachment:"

# Range used when pulling glpi_documenttypes in one call at preflight.
DOCUMENT_TYPE_FETCH_RANGE = "0-999"

# Deduplication marker for notes, written as the FIRST LINE of Notepad.content.
#
# A Notepad row has no comment column and no field of its own to hide a marker
# in, so the marker rides in the visible content. It stays on a line by itself
# because `_has_exact_marker` compares whole lines - `rdmnote:1559` must not
# match `rdmnote:155016`, the same substring trap `rdmattachment:` has.
#
# Note the asymmetry with documents: a Notepad row dies with its host item,
# while a Document survives the deletion of the project it was linked to. So
# this marker proves "this note is already on THIS item", never "this note
# exists somewhere in GLPI" - which is all the dedup needs.
NOTE_MARKER_PREFIX = "rdmnote:"

# GLPI answers a list GET with only the first 15 rows unless a range is given.
# Measured 2026-08-10 against project 1277: it has 19 linked documents and the
# read-back reported 15. Anything that must see EVERY row of a search - the
# Document_Item links of one item, above all - has to ask for a range.
SEARCH_FETCH_RANGE = "0-999"

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

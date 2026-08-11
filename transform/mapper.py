"""Apply mapping.yml to a Redmine issue and account for every single field.

Design rule behind this module (spec section 13, rule 7): reporting skipped
fields is the primary functional requirement, not an add-on. Therefore the
mapper never just builds a payload - it returns a FieldRecord for every field it
touched AND for every custom field present in the source that no mapping entry
consumed. Nothing can disappear silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from config.settings import (
    PLUGIN_CONTAINER_SECTIONS,
    PLUGIN_TEXT_MAX_LENGTH,
    TRACKER_TO_PROJECTTASKTYPE,
)

DATE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# yesno transform (spec 6.2). Redmine sends text, GLPI expects 0/1.
YESNO_FALSE = {"não", "nao", "no", "n", "0", "false"}
YESNO_TRUE = {"sim", "yes", "y", "1", "true"}

# Mandatory header for the task custom-field dump (spec 9.4). It is what tells
# a reader that the text below came from the migration rather than being typed
# into GLPI by hand - do not translate or reword it.
TASK_COMMENT_HEADER = "[Campos migrados do Redmine]"

# Shown on the record of a value that had to be cut to fit a plugin column.
# Lives here rather than in report.messages because it is the mapper's own
# explanation of what it did, exactly like the other _transform details.
TRUNCATED_DETAIL = (
    "valor cortado para caber na coluna do GLPI: {original} caracteres na "
    "origem, {limit} gravados"
)


class Outcome(str, Enum):
    WRITTEN = "written"                # goes into the GLPI payload
    EMPTY_SOURCE = "empty_source"      # column exists on both sides, source empty
    NO_COUNTERPART = "no_counterpart"  # Redmine field with nowhere to go in GLPI
    UNRESOLVED = "unresolved"          # dropdown / status / user lookup failed
    NEVER_WRITE = "never_write"        # deliberately not written


@dataclass
class FieldRecord:
    source_label: str
    outcome: Outcome
    raw_value: str = ""
    target_column: str = ""
    written_value: Any = None
    detail: str = ""
    mandatory: bool = False
    # The value WAS written, but it did not fit the GLPI column and was cut to
    # PLUGIN_TEXT_MAX_LENGTH. Deliberately a flag rather than an Outcome: the
    # field did reach GLPI, so it belongs in the WRITTEN bucket and the
    # section-7 arithmetic must not change. The report gives it its own block.
    truncated: bool = False
    original_length: int = 0
    # Which Redmine issue this field came from. Empty means the root issue;
    # set for tasks and Faturamento so a report line is never ambiguous when
    # the same field name appears on several issues (e.g. "Cliente").
    origin: str = ""


@dataclass
class MappingResult:
    payload: dict = field(default_factory=dict)
    records: list[FieldRecord] = field(default_factory=list)

    def by_outcome(self, outcome: Outcome) -> list[FieldRecord]:
        return [record for record in self.records if record.outcome is outcome]

    @property
    def missing_mandatory(self) -> list[FieldRecord]:
        """Mandatory GLPI columns that ended up without a value (spec 11.1)."""
        return [
            record
            for record in self.records
            if record.mandatory and record.outcome is not Outcome.WRITTEN
        ]


def normalise_name(name: str) -> str:
    """Collapse whitespace + casefold, for matching custom field names."""
    return " ".join(str(name or "").split()).casefold()


def coerce_text(value: Any) -> str:
    """Flatten a Redmine value to text.

    .strip() is mandatory - real data carries leading spaces (" 5593.40").
    Numeric values are NEVER parsed: 'Valor do Projeto' uses the Brazilian
    convention (66.977,45) while 'Valor Total da NF' uses the US one (5593.40),
    and both target GLPI text columns. Parsing would desync a project from its
    own invoice.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(coerce_text(item) for item in value if item not in (None, ""))
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def read_attribute(issue: dict, path: str) -> Any:
    """Read a dotted path such as 'status.id' or 'assigned_to.id'."""
    node: Any = issue
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def custom_field_index(issue: dict) -> dict[str, dict]:
    """Custom fields keyed by normalised name.

    GOVERNING RULE (spec section 4): match by name, never by id. Custom field
    ids differ per tracker - "Situação Faturamento" is id 41 while id 9 is
    "Ano de Início", so an id-based mapping would write a year into the billing
    status field.
    """
    index: dict[str, dict] = {}
    for entry in issue.get("custom_fields") or []:
        if isinstance(entry, dict) and entry.get("name"):
            index.setdefault(normalise_name(entry["name"]), entry)
    return index


class Mapper:
    def __init__(self, mapping: dict, status_resolver, user_resolver, dropdown_resolver):
        self._mapping = mapping
        self._status = status_resolver
        self._users = user_resolver
        self._dropdowns = dropdown_resolver

    # -- public API --------------------------------------------------------

    def map_section(self, issue: dict, section: str, sweep: bool = True) -> MappingResult:
        """Map one mapping.yml section (project_core, container15, ...).

        `sweep=True` adds the exhaustive pass over custom fields that no entry
        consumed - that is what guarantees an unknown field cannot vanish.
        """
        result = MappingResult()
        entries = self._mapping.get(section) or []
        cf_index = custom_field_index(issue)
        consumed: set[str] = set()

        for entry in entries:
            self._map_entry(issue, entry, cf_index, consumed, result, section)

        if sweep:
            self._sweep_unmapped(section, cf_index, consumed, result)
        return result

    def map_task(self, issue: dict) -> MappingResult:
        """Core task columns plus the generic custom-field dump (spec 9.4).

        Descendants carry their own custom fields and the set DEPENDS ON THE
        TRACKER (Compras brings 7, Subtarefa Cemig brings 2), while none of them
        has a counterpart in glpi_projecttasks - containers 15 and 25 attach to
        Project only. Hard-coding a list would therefore lose data the moment a
        new tracker appears, so we iterate over issue.custom_fields generically.

        Tracker 41 (Compras) entering scope on 2026-08-06 is the case in point:
        its seven fields - Cliente, Data Finalização, Data Cotação, Solicitação
        Interna, Código Solicitação Interna, Data Aprovação FP&A, Pedido - reach
        the comment dump through this loop, with no entry added anywhere.

        Closed decision (variant b, "comment"): every non-empty field is both
        reported AND dumped into the task's comment under a mandatory header
        that distinguishes migrated data from text typed by hand in GLPI.
        """
        result = self.map_section(issue, "task_core", sweep=False)

        dumped: list[str] = []
        for entry in issue.get("custom_fields") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") or ""
            value = coerce_text(entry.get("value"))
            if value:
                dumped.append(f"{name}: {value}")
                result.records.append(
                    FieldRecord(
                        source_label=name,
                        outcome=Outcome.NO_COUNTERPART,
                        raw_value=value,
                        detail="sem equivalente no GLPI — gravado no comentário da tarefa",
                    )
                )
            else:
                result.records.append(
                    FieldRecord(
                        source_label=name,
                        outcome=Outcome.EMPTY_SOURCE,
                        detail="sem valor no Redmine",
                    )
                )

        if dumped:
            result.payload["comment"] = "\n".join([TASK_COMMENT_HEADER, *dumped])

        self._tag_origin(result, issue)
        return result

    def map_faturamento(self, issue: dict) -> tuple[MappingResult, MappingResult]:
        """One tracker-15 issue -> a ProjectTask plus its container-26 row.

        CHANGED 2026-08-06 (spec 6.5). A Faturamento used to be a single
        container-25 row on the Project. GLPI now models it as a ProjectTask of
        type Faturamento carrying a container-26 row, so this returns the same
        (core, container) pair map_project already returns - the shapes stay
        parallel on purpose.

        The sweep runs once, on the container pass, because both sections draw
        from the same pool of custom fields.
        """
        core = self.map_section(issue, "task_core", sweep=False)
        container = self.map_section(issue, "container26", sweep=False)

        cf_index = custom_field_index(issue)
        consumed = self._consumed_names(("task_core", "container26"))
        # scope 'faturamento' in mapping.yml declares Cliente and Conformidade1.
        self._sweep_unmapped("faturamento", cf_index, consumed, container)
        self._tag_origin(core, issue)
        self._tag_origin(container, issue)
        return core, container

    def map_project(self, issue: dict) -> tuple[MappingResult, MappingResult]:
        """Core project columns and container-15 columns.

        The sweep runs once, on the container-15 pass, because both sections
        draw from the same pool of custom fields.
        """
        core = self.map_section(issue, "project_core", sweep=False)
        container = self.map_section(issue, "container15", sweep=False)

        cf_index = custom_field_index(issue)
        consumed = self._consumed_names(("project_core", "container15"))
        self._sweep_unmapped("project", cf_index, consumed, container)
        return core, container

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _tag_origin(result: MappingResult, issue: dict) -> None:
        """Stamp each record with the issue it came from.

        Without this, "Cliente" reported for the project and "Cliente" reported
        for its Faturamento are indistinguishable in the report.
        """
        label = f"RDM {issue.get('id')}"
        for record in result.records:
            if not record.origin:
                record.origin = label

    def _consumed_names(self, sections) -> set[str]:
        names: set[str] = set()
        for section in sections:
            for entry in self._mapping.get(section) or []:
                for source in entry.get("sources") or []:
                    if source.get("from") == "custom_field":
                        names.add(normalise_name(source.get("name", "")))
        return names

    def _map_entry(
        self,
        issue: dict,
        entry: dict,
        cf_index: dict[str, dict],
        consumed: set[str],
        result: MappingResult,
        section: str = "",
    ) -> None:
        column = entry["column"]
        transform = entry.get("transform", "text")
        mandatory = bool(entry.get("mandatory"))

        raw = ""
        label = column
        for source in entry.get("sources") or []:
            kind = source.get("from")
            if kind == "issue_id":
                raw, label = coerce_text(issue.get("id")), "issue.id"
            elif kind == "attribute":
                raw = coerce_text(read_attribute(issue, source["name"]))
                label = f"issue.{source['name']}"
            elif kind == "custom_field":
                name = source["name"]
                consumed.add(normalise_name(name))
                found = cf_index.get(normalise_name(name))
                raw = coerce_text(found.get("value")) if found else ""
                label = name
            # First source with a value wins; keep looking otherwise.
            if raw:
                break

        # Appendix D: use falsiness, not == "". Real data has null where other
        # issues have "" (issue 17306, Conformidade1).
        if not raw:
            result.records.append(
                FieldRecord(
                    source_label=label,
                    outcome=Outcome.EMPTY_SOURCE,
                    target_column=column,
                    mandatory=mandatory,
                    detail="sem valor no Redmine",
                )
            )
            return

        value, outcome, detail = self._transform(entry, transform, raw, label)

        # A plugin "text" column is a VARCHAR(255). Over the limit, GLPI commits
        # the project and THEN fails on the plugin's own INSERT, leaving a
        # project with no container row and therefore no rdmfield marker - see
        # PLUGIN_TEXT_MAX_LENGTH. Cutting the value here is what keeps the
        # project writable at all; the loss is reported, never silent.
        truncated = False
        original_length = 0
        if (
            outcome is Outcome.WRITTEN
            and transform == "text"
            and section in PLUGIN_CONTAINER_SECTIONS
            and isinstance(value, str)
            and len(value) > PLUGIN_TEXT_MAX_LENGTH
        ):
            original_length = len(value)
            value = value[:PLUGIN_TEXT_MAX_LENGTH]
            truncated = True
            detail = TRUNCATED_DETAIL.format(
                original=original_length, limit=PLUGIN_TEXT_MAX_LENGTH
            )

        if outcome is Outcome.WRITTEN:
            result.payload[column] = value
        result.records.append(
            FieldRecord(
                source_label=label,
                outcome=outcome,
                raw_value=raw,
                target_column=column,
                written_value=value if outcome is Outcome.WRITTEN else None,
                detail=detail,
                mandatory=mandatory,
                truncated=truncated,
                original_length=original_length,
            )
        )

    def _transform(
        self, entry: dict, transform: str, raw: str, label: str
    ) -> tuple[Any, Outcome, str]:
        if transform == "text":
            return raw, Outcome.WRITTEN, ""

        if transform == "date":
            match = DATE_PATTERN.match(raw)
            if not match:
                return None, Outcome.UNRESOLVED, f"formato de data inválido: {raw!r}"
            return match.group(1), Outcome.WRITTEN, ""

        if transform == "integer":
            try:
                return int(float(raw)), Outcome.WRITTEN, ""
            except (TypeError, ValueError):
                return None, Outcome.UNRESOLVED, f"valor numérico inválido: {raw!r}"

        if transform == "yesno":
            key = raw.strip().casefold()
            if key in YESNO_FALSE:
                return 0, Outcome.WRITTEN, ""
            if key in YESNO_TRUE:
                return 1, Outcome.WRITTEN, ""
            # Never write raw text into a yesno column (spec 6.2).
            return None, Outcome.UNRESOLVED, f"valor sim/não não reconhecido: {raw!r}"

        if transform == "status":
            resolved = self._status.resolve(raw)
            if resolved is None:
                return None, Outcome.UNRESOLVED, f"status {raw!r} fora do mapa de status"
            return resolved, Outcome.WRITTEN, ""

        if transform == "user":
            resolved = self._users.resolve(raw)
            if resolved is None:
                login = self._users.login(raw)
                if self._users.is_known(raw):
                    detail = f"usuário {login!r} (RDM {raw}) não possui conta no GLPI"
                else:
                    detail = f"usuário RDM {raw} não está no mapa de usuários"
                return None, Outcome.UNRESOLVED, detail
            return resolved, Outcome.WRITTEN, ""

        if transform == "tasktype":
            # Redmine tracker id -> glpi_projecttasktypes. Only the trackers in
            # TRACKER_TO_PROJECTTASKTYPE get a type; 14 and 42 deliberately get
            # none, so NO_COUNTERPART (not UNRESOLVED) is the honest outcome -
            # nothing failed, there simply is no type for a plain task.
            try:
                tracker_id = int(float(raw))
            except (TypeError, ValueError):
                return None, Outcome.UNRESOLVED, f"tracker inválido: {raw!r}"
            resolved = TRACKER_TO_PROJECTTASKTYPE.get(tracker_id)
            if resolved is None:
                return (
                    None,
                    Outcome.NO_COUNTERPART,
                    f"tracker {tracker_id} não possui tipo de tarefa no GLPI",
                )
            return resolved, Outcome.WRITTEN, ""

        if transform == "dropdown":
            itemtype = entry["itemtype"]
            resolved = self._dropdowns.resolve(itemtype, label, raw)
            if resolved is None:
                empty = self._dropdowns.dictionary_is_empty(itemtype)
                reason = "dicionário vazio no GLPI" if empty else "valor ausente no dicionário do GLPI"
                return None, Outcome.UNRESOLVED, f"{raw!r} — {reason}"
            return resolved, Outcome.WRITTEN, ""

        return raw, Outcome.WRITTEN, ""

    def _sweep_unmapped(
        self,
        scope: str,
        cf_index: dict[str, dict],
        consumed: set[str],
        result: MappingResult,
    ) -> None:
        """Account for every custom field no mapping entry claimed.

        This is the safety net the whole design rests on: a field the spec never
        anticipated still surfaces in the report instead of being dropped.
        """
        declared = {
            normalise_name(item["name"]): item.get("reason", "")
            for item in (self._mapping.get("report_only") or {}).get(scope, [])
        }

        for key, entry in cf_index.items():
            if key in consumed:
                continue
            raw = coerce_text(entry.get("value"))
            reason = declared.get(key)
            detail = {
                "manual": "definido manualmente no GLPI após a migração",
                "no_counterpart": "sem equivalente no GLPI",
                "unverified": "significado não confirmado — não mapeado (a verificar)",
            }.get(reason or "", "sem equivalente no GLPI — campo não previsto no mapeamento")

            result.records.append(
                FieldRecord(
                    source_label=entry.get("name", key),
                    outcome=Outcome.NO_COUNTERPART if raw else Outcome.EMPTY_SOURCE,
                    raw_value=raw,
                    detail=detail,
                )
            )

    # -- columns we deliberately never write --------------------------------

    def never_write_records(self) -> list[FieldRecord]:
        """Declared columns that are intentionally left untouched (spec 6.4)."""
        detail_by_reason = {
            # Verified in plugin source 1.24.3 (2026-08-06): a write to an
            # inactive column is NOT rejected, it just becomes invisible in the
            # GLPI form. The wording used to claim it was an error.
            "inactive": "coluna inativa no GLPI (is_active: 0) — o valor não apareceria no formulário",
            "manual": "definido manualmente no GLPI após a migração",
            "skipped": "prioridade é ignorada em toda a migração",
            "redundant": (
                "mesma informação já gravada no campo Estado da tarefa "
                "(projectstates_id)"
            ),
        }
        records = []
        for item in self._mapping.get("never_write") or []:
            records.append(
                FieldRecord(
                    source_label=item["column"],
                    outcome=Outcome.NEVER_WRITE,
                    target_column=item["column"],
                    detail=detail_by_reason.get(item.get("reason", ""), ""),
                )
            )
        return records

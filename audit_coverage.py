"""Dictionary coverage audit - read-only, writes nothing anywhere.

Spec Appendix C: "before production, dump every unique value of these fields
from all tracker-14 issues and compare against the GLPI dictionaries. That is
the only way to avoid discovering gaps one at a time in production."

This answers a single question: which values currently in Redmine would be
dropped because the matching GLPI dictionary entry does not exist?

    python audit_coverage.py                 # tracker 14 (default)
    python audit_coverage.py --tracker 42

collect_coverage() does the work and emits the very same lines this script has
always printed, while also returning them as data. The CLI passes emit=print;
the web panel passes its own emit and renders the returned structure as a table.
One code path, one wording.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.errors import ApiError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402
from config.settings import ConfigError, Settings, load_settings, load_yaml  # noqa: E402
from report import messages  # noqa: E402
from transform.mapper import coerce_text, normalise_name  # noqa: E402

WIDTH = 80


@dataclass
class MissingValue:
    """A Redmine value with no matching entry in the GLPI dictionary."""

    value: str
    count: int


@dataclass
class FieldCoverage:
    field_name: str
    itemtype: str
    dictionary_size: int = 0
    distinct_values: int = 0
    covered: int = 0
    missing: list[MissingValue] = field(default_factory=list)
    error: str = ""


@dataclass
class CoverageResult:
    tracker: int
    total_issues: int = 0
    fields: list[FieldCoverage] = field(default_factory=list)
    missing_total: int = 0
    affected_issues: int = 0

    @property
    def complete(self) -> bool:
        return self.missing_total == 0


def dropdown_entries(mapping: dict) -> list[tuple[str, str]]:
    """(Redmine custom field name, GLPI dictionary itemtype) pairs."""
    pairs: list[tuple[str, str]] = []
    for section in ("container15", "container25"):
        for entry in mapping.get(section) or []:
            if entry.get("transform") != "dropdown":
                continue
            for source in entry.get("sources") or []:
                if source.get("from") == "custom_field":
                    pairs.append((source["name"], entry["itemtype"]))
    return pairs


def collect_coverage(
    settings: Settings,
    mapping: dict,
    tracker: int,
    emit=print,
) -> CoverageResult:
    """Read the tracker, compare every dropdown value against GLPI, report.

    Emits exactly the lines this script has always printed. Raises ApiError,
    which the caller renders through messages.redact().
    """
    pairs = dropdown_entries(mapping)
    wanted = {normalise_name(name): (name, itemtype) for name, itemtype in pairs}
    result = CoverageResult(tracker=tracker)

    # value counts per Redmine field name
    observed: dict[str, Counter] = defaultdict(Counter)

    emit("=" * WIDTH)
    emit(f"AUDITORIA DE COBERTURA DOS DICIONÁRIOS — tracker {tracker}")
    emit("=" * WIDTH)
    emit("Lendo o Redmine…")

    with RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
        for issue in redmine.iter_issues(tracker):
            result.total_issues += 1
            for custom in issue.get("custom_fields") or []:
                key = normalise_name(custom.get("name", ""))
                if key not in wanted:
                    continue
                value = coerce_text(custom.get("value"))
                if value:
                    observed[key][value] += 1

    emit(f"Issues lidas: {result.total_issues}")
    emit("")

    with GlpiClient(
        settings.glpi_url, settings.glpi_user_token, settings.glpi_app_token
    ) as glpi:
        for key, (field_name, itemtype) in sorted(wanted.items()):
            counter = observed.get(key)
            if not counter:
                continue
            try:
                dictionary = glpi.load_dropdown(itemtype)
            except ApiError as exc:
                detail = messages.redact(exc)
                result.fields.append(
                    FieldCoverage(field_name=field_name, itemtype=itemtype, error=detail)
                )
                emit(f"[ERRO] {field_name}: {detail}")
                continue

            covered, missing = [], []
            for value, count in counter.most_common():
                target = glpi.resolve_dropdown(itemtype, value)
                (covered if target is not None else missing).append((value, count))

            coverage = FieldCoverage(
                field_name=field_name,
                itemtype=itemtype,
                dictionary_size=len(dictionary),
                distinct_values=len(counter),
                covered=len(covered),
                missing=[MissingValue(value=value, count=count) for value, count in missing],
            )
            result.fields.append(coverage)

            status = "OK" if not missing else "FALTAM VALORES"
            emit("-" * WIDTH)
            emit(f"{field_name}  [{status}]")
            emit(f"  dicionário GLPI: {itemtype} ({len(dictionary)} entrada(s))")
            emit(
                f"  valores distintos no Redmine: {len(counter)} "
                f"— cobertos: {len(covered)}, ausentes: {len(missing)}"
            )
            for value, count in missing:
                result.missing_total += 1
                result.affected_issues += count
                emit(f"    [PERDIDO] {value!r} — usado em {count} issue(s)")

    emit("-" * WIDTH)
    emit("")
    emit("=" * WIDTH)
    if result.missing_total:
        emit(
            f"RESULTADO: {result.missing_total} valor(es) distinto(s) não existem nos "
            f"dicionários do GLPI,\nafetando {result.affected_issues} preenchimento(s) "
            f"em {result.total_issues} issue(s) do tracker {tracker}."
        )
        emit(
            "\nEstes valores NÃO serão migrados (a migração nunca cria entradas "
            "de dicionário\nautomaticamente). Cadastre-os no GLPI em "
            "Configurar → Campos adicionais\nantes de rodar com --apply, "
            "ou aceite a perda de forma consciente."
        )
    else:
        emit(
            "RESULTADO: cobertura completa. Todos os valores de lista usados no "
            "Redmine\nexistem nos dicionários do GLPI."
        )
    emit("=" * WIDTH)

    return result


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description=(
            "Audita a cobertura dos dicionários do GLPI: lista os valores que "
            "existem no Redmine e seriam perdidos na migração. Não grava nada."
        )
    )
    parser.add_argument("--tracker", type=int, default=14, help="Tracker do Redmine.")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        mapping = load_yaml("mapping.yml")
    except ConfigError as exc:
        print(messages.CONFIG_MISSING_VARS.format(names=exc), file=sys.stderr)
        return 2

    messages.register_secrets(settings.secret_values())

    try:
        result = collect_coverage(settings, mapping, args.tracker)
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return 1

    return 0 if result.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())

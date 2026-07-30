"""Dictionary coverage audit - read-only, writes nothing anywhere.

Spec Appendix C: "before production, dump every unique value of these fields
from all tracker-14 issues and compare against the GLPI dictionaries. That is
the only way to avoid discovering gaps one at a time in production."

This answers a single question: which values currently in Redmine would be
dropped because the matching GLPI dictionary entry does not exist?

    python audit_coverage.py                 # tracker 14 (default)
    python audit_coverage.py --tracker 42
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clients.errors import ApiError  # noqa: E402
from clients.glpi import GlpiClient  # noqa: E402
from clients.redmine import RedmineClient  # noqa: E402
from config.settings import ConfigError, load_settings, load_yaml  # noqa: E402
from report import messages  # noqa: E402
from transform.mapper import coerce_text, normalise_name  # noqa: E402

WIDTH = 80


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
    pairs = dropdown_entries(mapping)
    wanted = {normalise_name(name): (name, itemtype) for name, itemtype in pairs}

    # value counts per Redmine field name
    observed: dict[str, Counter] = defaultdict(Counter)
    total_issues = 0

    print("=" * WIDTH)
    print(f"AUDITORIA DE COBERTURA DOS DICIONÁRIOS — tracker {args.tracker}")
    print("=" * WIDTH)
    print("Lendo o Redmine…")

    try:
        with RedmineClient(settings.redmine_url, settings.redmine_api_key) as redmine:
            for issue in redmine.iter_issues(args.tracker):
                total_issues += 1
                for custom in issue.get("custom_fields") or []:
                    key = normalise_name(custom.get("name", ""))
                    if key not in wanted:
                        continue
                    value = coerce_text(custom.get("value"))
                    if value:
                        observed[key][value] += 1
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return 1

    print(f"Issues lidas: {total_issues}")
    print()

    missing_total = 0
    affected_issues = 0

    try:
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
                    print(f"[ERRO] {field_name}: {messages.redact(exc)}")
                    continue

                covered, missing = [], []
                for value, count in counter.most_common():
                    target = glpi.resolve_dropdown(itemtype, value)
                    (covered if target is not None else missing).append((value, count))

                status = "OK" if not missing else "FALTAM VALORES"
                print("-" * WIDTH)
                print(f"{field_name}  [{status}]")
                print(f"  dicionário GLPI: {itemtype} ({len(dictionary)} entrada(s))")
                print(
                    f"  valores distintos no Redmine: {len(counter)} "
                    f"— cobertos: {len(covered)}, ausentes: {len(missing)}"
                )
                for value, count in missing:
                    missing_total += 1
                    affected_issues += count
                    print(f"    [PERDIDO] {value!r} — usado em {count} issue(s)")

        print("-" * WIDTH)
        print()
        print("=" * WIDTH)
        if missing_total:
            print(
                f"RESULTADO: {missing_total} valor(es) distinto(s) não existem nos "
                f"dicionários do GLPI,\nafetando {affected_issues} preenchimento(s) "
                f"em {total_issues} issue(s) do tracker {args.tracker}."
            )
            print(
                "\nEstes valores NÃO serão migrados (a migração nunca cria entradas "
                "de dicionário\nautomaticamente). Cadastre-os no GLPI em "
                "Configurar → Campos adicionais\nantes de rodar com --apply, "
                "ou aceite a perda de forma consciente."
            )
        else:
            print(
                "RESULTADO: cobertura completa. Todos os valores de lista usados no "
                "Redmine\nexistem nos dicionários do GLPI."
            )
        print("=" * WIDTH)
    except ApiError as exc:
        print(messages.redact(exc), file=sys.stderr)
        return 1

    return 0 if missing_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

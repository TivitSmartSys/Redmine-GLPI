"""Launcher for the web panel.

    python serve.py                    # http://127.0.0.1:8000, opens the browser
    python serve.py --port 8123
    python serve.py --no-browser --db /caminho/migration.db

Binds to the loopback interface only. The panel can write to GLPI, so it must
not be reachable from the network: the confirmation gate protects the operator
from a mistake, not the host from a stranger.
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import ConfigError, DEFAULT_DB_PATH  # noqa: E402
from report import messages  # noqa: E402

EXIT_CONFIG = 2

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    parser = argparse.ArgumentParser(
        prog="serve.py",
        description=(
            "Abre o painel web da migração Redmine → GLPI no navegador. "
            "Escuta somente em 127.0.0.1."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Porta HTTP.")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Caminho do banco SQLite.")
    parser.add_argument(
        "--no-browser", action="store_true", help="Não abrir o navegador automaticamente."
    )
    args = parser.parse_args(argv)

    try:
        from web.server import create_app

        app = create_app(db_path=args.db)
    except ConfigError as exc:
        detail = str(exc)
        if detail == "REDMINE_URL":
            print(messages.CONFIG_REDMINE_URL_INVALID, file=sys.stderr)
        else:
            print(messages.CONFIG_MISSING_VARS.format(names=detail), file=sys.stderr)
        return EXIT_CONFIG
    except ImportError:
        print(
            "Flask não está instalado. Execute: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    url = f"http://{DEFAULT_HOST}:{args.port}/"
    print(f"Painel disponível em {url}")
    print("Pressione Ctrl+C para encerrar.")

    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()

    try:
        # threaded=True is required: a job holds a worker thread while the SSE
        # connection streams it, and the confirmation arrives on a third.
        app.run(host=DEFAULT_HOST, port=args.port, threaded=True, debug=False)
    except KeyboardInterrupt:  # pragma: no cover
        print(messages.CLI_INTERRUPTED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

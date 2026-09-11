# Deployment handoff — Redmine → GLPI migration tool

Everything an operator (or a deployment agent) needs to run this application on a VM.
Written against the repository state of 2026-08-05, revised 2026-09-10 for the
production instance and the batch/panel work that landed after it.

The functional specification is `INSTRUKCJA_Redmine_do_GLPI_2.md`; architecture and
invariants are in `CLAUDE.md`. **This document does not restate them** — it covers
only runtime, packaging, networking, state and security.

---

## 1. What is being deployed

A Python application with **several entrypoints over one shared code path**:

| Entrypoint | Command | Nature |
|---|---|---|
| CLI | `python main.py --issue <id> [--apply]` | one-shot, exits |
| Batch CLI | `python migrate_batch.py --project <p> [--apply]` | long one-shot, resumable |
| Web panel | `python serve.py` (Flask) | long-running HTTP service |
| Audit CLI | `python audit_coverage.py [--tracker N]` | one-shot, read-only |
| Status CLI | `python migration_status.py [--html <f>]` | one-shot, read-only |
| Reset CLI | `python reset_migration.py --issue <id>` | one-shot, deletes only markers |

`purge_import.py` is **retired**: it refuses to run and exits 2. It was built
for the test instance and would select over a thousand legitimate projects here.
Do not deploy a wrapper that tries to work around it.

`web/jobs.py` is a transcription of `main.main()` — the panel calls the same
functions in the same order. Deploying the panel therefore also deploys the CLI;
they are not separate artifacts.

**Exit codes (both CLIs):** `0` success · `1` failure/aborted · `2` configuration error.

### What it does to the outside world

The tool **writes to a production GLPI instance** (creates Projects, ProjectTasks
and Fields-plugin rows). Default mode is dry-run; writing requires `--apply` plus a
typed confirmation. Treat every deployment decision below as protecting that
capability.

---

## 2. Runtime requirements

- **Python 3.12** (development and verification baseline: 3.12.0).
  3.11 will work — all modules using `X | Y` annotations carry
  `from __future__ import annotations`. Do not go below 3.10.
- **No build step, no compiled extensions, no system packages** beyond Python
  itself and a CA bundle (`certifi` ships with `requests`).
- **No database server.** State is a local SQLite file.
- Dependencies (`requirements.txt`, unpinned by design):

  ```
  requests>=2.31.0
  python-dotenv>=1.0.0
  PyYAML>=6.0
  Flask>=3.0.0
  ```

  Versions verified working in the dev venv: `requests 2.34.2`, `python-dotenv 1.2.2`,
  `PyYAML 6.0.3`, `Flask 3.1.3`, `Werkzeug 3.1.8`, `urllib3 2.7.0`.

- **`pytest` is a dev dependency and is NOT in `requirements.txt`.** Install it
  separately if you want to run the test suite on the VM (`pip install pytest`).
- **A production WSGI server is NOT in `requirements.txt`.** See §6 — add
  `gunicorn` (Linux) or `waitress` (Windows) to the deployment environment.

---

## 3. Configuration — `.env`

Five variables, all required. `config/settings.load_settings()` reads them from the
environment, falling back to `<repo root>/.env`, and raises `ConfigError` listing the
**names only** of anything missing → the CLI exits `2`.

| Variable | Meaning |
|---|---|
| `REDMINE_URL` | Redmine base URL. **Bare host — must NOT contain `apirest.php`** |
| `REDMINE_API_KEY` | Redmine API key (sent as `X-Redmine-API-Key`) |
| `GLPI_URL` | GLPI API endpoint, **including `apirest.php`** |
| `GLPI_USER_TOKEN` | GLPI `user_token` |
| `GLPI_APP_TOKEN` | GLPI `App-Token` |

Values from `.env.example` (the real instances used so far):

```
REDMINE_URL=http://172.178.61.88
GLPI_URL=https://smartsystems-apps.brazilsouth.cloudapp.azure.com/apirest.php
```

Notes for the deployment:

- `load_settings()` **rejects** a `REDMINE_URL` containing `apirest.php` — this is
  the classic copy/paste error and it fails fast with exit code `2`.
- `load_dotenv(..., override=False)`: **real environment variables win over `.env`.**
  Injecting secrets as systemd `Environment=`/`EnvironmentFile=` or container env
  works and takes precedence — a stale `.env` on disk will not shadow them.
- `.env` is git-ignored and **must never be committed or baked into an image**.
  Ship it out-of-band; on the VM it should be `chmod 600`, owned by the service user.
- Secrets never reach logs: everything user-facing passes through
  `report.messages.redact()`, and `Settings.__repr__` is `Settings(<redacted>)`.
  Do not add debug logging that bypasses this.

---

## 4. Outbound network requirements

The VM must be able to reach **both** systems. There is no inbound requirement for
the CLI at all.

| Target | Host | Port/Proto | Notes |
|---|---|---|---|
| Redmine (source, read-only) | `172.178.61.88` | **80 / HTTP** | plaintext |
| GLPI (target, read + write) | `smartsystems-apps.brazilsouth.cloudapp.azure.com` | 443 / HTTPS | Azure |

- HTTP timeout is **60 s per request** (`HTTP_TIMEOUT_SECONDS`), applied by both
  clients. A migration of a large tree issues many sequential requests — do not put
  an aggressive idle-kill firewall rule between the VM and either host.
- Dropdown dictionaries are fetched with range `0-999` in a single GET each, during
  preflight.
- TLS verification is `requests`' default (**enabled**). If the GLPI certificate is
  not publicly trusted on the VM, install the CA into the system trust store —
  **do not** add `verify=False` to the client.
- **Redmine traffic is plain HTTP**, so `REDMINE_API_KEY` crosses the wire in a
  header in cleartext. If the VM is not on the same trusted network as `172.178.61.88`,
  raise this with the platform owner (VPN / private peering / enabling TLS on Redmine).
  It is a pre-existing property of the environment, not something this deployment
  introduces — but a VM move is the moment it changes exposure.
- GLPI sessions are opened per run (`initSession` → `killSession` via context
  manager). Nothing is kept warm between runs; no session file to persist.

---

## 5. Persistent state and filesystem

| Path | What | Must persist? |
|---|---|---|
| `migration.db` (repo root by default) | SQLite `migration_map` — idempotence cache + crash guard, and the source of the panel's **History** tab | Yes (see below) |
| `report_<issue>_<timestamp>.txt` | written **into the current working directory** when `--report` is passed | Optional, but archive them |
| `reports/<run-id>/` | per-item reports and `resumo.txt` from a batch run — written by `migrate_batch.py` **and by the panel's Lote view**, and read back by the panel's item-report links | Yes, if the links are to keep working |
| `reports/status-cache.json` + `status.html` | the Progresso tab's saved reading — written by its "Atualizar" job and by `migration_status.py --html` | Yes; without it the tab is empty until the next scan |
| `.env` | credentials | Yes, secret |
| `config/*.yml` | `mapping.yml`, `status_map.yml`, `user_map.yml` — versioned, read at startup | Ships with the code |

- Override the DB location with `--db /path/to/migration.db` (both `main.py` and
  `serve.py`). Put it on a persistent volume outside the deploy directory if
  deployments replace the checkout — e.g. `/var/lib/redmine-glpi/migration.db`.
- **`MIGRATION_REPORTS_DIR` must be set, and a hardened unit will not work without
  it.** Everything a run writes lands in one directory, and it defaults to
  `reports` **relative to `WorkingDirectory`** — i.e. inside the application
  directory, which §7.3's `ProtectSystem=strict` mounts read-only. `reports/` is
  git-ignored, so a fresh checkout does not even contain it and the first write
  is a `mkdir`. Measured on the production VM 2026-09-10: pressing *Atualizar* in
  the Progresso tab answered `[Errno 30] Read-only file system: 'reports'`, and
  **the Lote view had the same fault waiting** — the same directory, one
  `mkdir` in `batch/runner.py`, reached only after preflight and a full pending
  sweep. Point the variable at the persistent volume:
  `MIGRATION_REPORTS_DIR=/var/lib/redmine-glpi/reports`. Since that release both
  jobs probe the directory (create it, write a file, delete it) **before** any
  GLPI or Redmine call and refuse with a message naming the path and this
  variable, so the failure costs a millisecond instead of a two-minute read.
- **Losing `migration.db` does not corrupt anything.** The authoritative dedup check
  is the `rdmfield` marker searched in GLPI itself (`check_already_migrated`); the DB
  is a cache. Losing it costs the History tab and resumability, not correctness.
- The flip side: **deleting the DB does not let you migrate an issue again either.**
  What refuses a re-run is the `rdmfield` marker on the project container's row, and that
  row survives the deletion of its project. To re-test an issue whose GLPI project
  was removed, use `python reset_migration.py --issue <id> --apply` — it purges the
  orphaned marker and the matching `migration_map` rows, and refuses if the project
  still exists. Clearing only one of the two caches is worse than clearing neither:
  without the DB rows the next run recreates everything, and without the marker but
  *with* the rows it creates a project whose tasks are all skipped.
- The service user needs **write access to the DB's directory** (SQLite creates
  journal files alongside), and to the CWD if reports are saved.
- **Working directory matters**: `default_report_path()` resolves relative to `.`,
  and the default DB path resolves relative to the repo root. Set `WorkingDirectory=`
  explicitly in the service unit.
- `config/` and the code itself can be read-only.
- Do **not** copy to the VM: `.venv/`, `__pycache__/`, `.pytest_cache/`,
  `report_*.txt`, and the dev `migration.db` (it contains this workstation's
  migration history). All are already git-ignored.

---

## 6. Serving the web panel — the constraints that matter

Read this section before writing any service unit. Four properties of the app are
non-negotiable inputs to the deployment.

### 6.1 It binds to loopback only, and has NO authentication

`serve.py` hardcodes `DEFAULT_HOST = "127.0.0.1"` and exposes **no `--host` flag**.
`web/server.py` states it plainly: *"There is no auth: this is a single-operator tool
running on the operator's own machine."*

The panel can write to production GLPI. Anyone who can reach the port can start an
`--apply` job and confirm it — the confirmation gate protects the operator from a
mistake, **not the host from a stranger**.

Two acceptable exposure models, in order of preference:

1. **No exposure — SSH port-forward.** Run the service on `127.0.0.1:8000`; the
   operator reaches it with `ssh -L 8000:127.0.0.1:8000 user@vm`. Nothing is opened
   in the VM's network security group. This is the recommended default.
2. **Reverse proxy with authentication + TLS.** nginx/Caddy terminating TLS,
   enforcing auth (basic auth at minimum, SSO if available), IP-allowlisted, proxying
   to `127.0.0.1:8000`. See §6.5 for the SSE-specific proxy settings.

**Do not** simply change the bind address to `0.0.0.0` and open the port. If the
deployment requires that, the app needs an authentication layer first — that is a
code change, not a config change.

### 6.2 Exactly ONE process. No multi-worker WSGI.

`JobManager` holds jobs in **in-process memory** (`self._jobs: dict[str, Job]`,
single-slot `self._current`, `threading.Event` confirmation gates). With more than
one worker process:

- `GET /api/jobs/<id>` and the confirm/cancel endpoints hit the wrong worker at
  random and return 404,
- the "one job at a time" lock stops being global,
- the SSE stream and the confirmation can land in different processes, so **a job
  waiting for confirmation can never be released**.

⇒ **`--workers 1`, always.** Do not enable autoscaling, do not run two instances
behind a load balancer, do not restart the service while a job is mid-run.

### 6.3 Threads are required

A running job holds a worker thread, the SSE connection streams it on a second, and
the confirmation arrives on a third. `serve.py` passes `threaded=True` for exactly
this reason. Any WSGI server must be threaded (`gunicorn --worker-class gthread` or
`waitress`), never a single-threaded sync worker.

### 6.4 Long-lived requests

- SSE streams (`GET /api/jobs/<id>/stream`) stay open for the whole job and emit a
  `: keep-alive` comment every **15 s** when idle.
- An `--apply` job blocks on the confirmation gate for up to **600 s**
  (`CONFIRM_TIMEOUT_SECONDS` in `web/jobs.py`) while holding the GLPI session open.

⇒ WSGI worker timeout must be disabled or set well above 600 s. Proxy read timeout
likewise (≥ 3600 s is safe).

### 6.5 Response buffering breaks the live console

The app already sends `Cache-Control: no-cache` and `X-Accel-Buffering: no`. nginx
honours the latter, but set it explicitly anyway:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_buffering off;          # required for SSE
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_set_header Connection '';
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### 6.6 Headless VM: disable the browser launch

`serve.py` calls `webbrowser.open()` 1 s after start. On a headless VM always pass
`--no-browser` (or use the WSGI entrypoint in §7, which never touches it).

---

## 7. Recommended deployment — Linux VM + systemd

### 7.1 WSGI entrypoint (must be created — it does not exist yet)

`serve.py` is a launcher, not a WSGI module. Add `wsgi.py` at the repo root:

```python
"""WSGI entrypoint for the migration panel. See DEPLOY.md."""
from web.server import create_app

app = create_app()  # raises ConfigError if .env is incomplete
```

Optionally pass a DB path: `create_app(db_path="/var/lib/redmine-glpi/migration.db")`.

### 7.2 Layout and install

```bash
sudo useradd --system --home /opt/redmine-glpi --shell /usr/sbin/nologin rdmglpi
sudo install -d -o rdmglpi -g rdmglpi /opt/redmine-glpi /var/lib/redmine-glpi

# code
sudo -u rdmglpi git clone <repo> /opt/redmine-glpi/app
cd /opt/redmine-glpi/app
sudo -u rdmglpi python3.12 -m venv .venv
sudo -u rdmglpi .venv/bin/pip install -r requirements.txt gunicorn

# secrets, out-of-band
sudo install -o rdmglpi -g rdmglpi -m 600 /path/to/.env /opt/redmine-glpi/app/.env
```

### 7.3 systemd unit — `/etc/systemd/system/redmine-glpi.service`

```ini
[Unit]
Description=Redmine to GLPI migration panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rdmglpi
Group=rdmglpi
WorkingDirectory=/opt/redmine-glpi/app
Environment=PYTHONUNBUFFERED=1
Environment=LANG=C.UTF-8
Environment=LC_ALL=C.UTF-8
# Both MUST point outside the application directory: ProtectSystem=strict below
# mounts it read-only, and every report, transcript and status cache is a write.
Environment=MIGRATION_DB_PATH=/var/lib/redmine-glpi/migration.db
Environment=MIGRATION_REPORTS_DIR=/var/lib/redmine-glpi/reports
# Secrets: prefer this over the on-disk .env (real env vars win, override=False).
EnvironmentFile=/etc/redmine-glpi/env
ExecStart=/opt/redmine-glpi/app/.venv/bin/gunicorn \
    --workers 1 \
    --worker-class gthread \
    --threads 8 \
    --timeout 0 \
    --graceful-timeout 30 \
    --bind 127.0.0.1:8000 \
    --access-logfile - --error-logfile - \
    wsgi:app
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/redmine-glpi /opt/redmine-glpi/app

[Install]
WantedBy=multi-user.target
```

`--timeout 0` is deliberate (§6.4). `--workers 1` is mandatory (§6.2).

**Restart policy caveat:** a restart kills any in-flight job. `Restart=on-failure`
(not `always`) plus never deploying during a migration window. There is no
resume-in-place: after a crash mid-apply, re-running the same issue is guarded by the
GLPI `rdmfield` dedup check, which refuses to touch an already-migrated project —
that case needs a human, not an automatic retry.

### 7.4 Running the CLI on the same VM

```bash
sudo -u rdmglpi /opt/redmine-glpi/app/.venv/bin/python \
     /opt/redmine-glpi/app/main.py --issue 20238 --report /var/lib/redmine-glpi/report.txt
```

For unattended pipelines add `--apply --yes` — `--yes` skips the interactive
confirmation. **Only wire that into automation with an explicit human decision**;
it removes the last guard before a production write.

Do not schedule the migration on a timer. It is a one-shot, per-issue, operator-driven
action by design.

---

## 8. Windows VM variant

If the VM must be Windows (matching the dev workstation):

- Serve with **waitress** instead of gunicorn — same single-process, threaded shape:

  ```powershell
  .venv\Scripts\python.exe -m waitress --host 127.0.0.1 --port 8000 --threads 8 wsgi:app
  ```

  (`pip install waitress`.) Waitress has no per-request timeout, which suits §6.4.
- Register it as a service with **NSSM** or `sc.exe`; set the working directory to
  the repo root and the log encoding to UTF-8.
- Console encoding: both CLIs call `stream.reconfigure(encoding="utf-8")` on stdout
  and stderr, so PT-BR accents survive `cmd.exe`. Keep stdout redirected to a file
  rather than a pipe with a legacy codepage.
- `.env` ACL: restrict to the service account only.

Everything in §§3–6 applies unchanged.

---

## 9. Post-deploy verification

Run in this order. Steps 1–4 write nothing.

1. **Config loads** — a missing variable exits `2` with names only:
   ```bash
   .venv/bin/python main.py --issue 20238   # exit 2 ⇒ .env problem
   ```
2. **Connectivity** — the panel's health endpoint checks both systems, opening a real
   authenticated request against each:
   ```bash
   curl -s http://127.0.0.1:8000/api/health
   # {"glpi":{"ok":true},"redmine":{"ok":true}}
   ```
3. **Dry-run smoke test** — `20238` is the minimal reference issue:
   ```bash
   .venv/bin/python main.py --issue 20238 --report /tmp/smoke.txt   # expect exit 0
   ```
   Preflight must pass; `ERROR_RIGHT_MISSING` on the Fields plugin is a hard stop and
   means the GLPI token lacks plugin rights.
4. **Panel** — open the tunnel, load `/`, confirm the live console streams (SSE not
   buffered), then check the **Configuração** tab shows all five credentials as
   *present* (values are never sent to the browser).
5. **Unit tests**, if `pytest` was installed:
   ```bash
   .venv/bin/python -m pytest tests -q
   ```
6. **Do not run `--apply` as a deployment smoke test.** It creates real objects in
   production GLPI, and v1 refuses to re-migrate an issue, so a throwaway write is
   not throwaway.

Wider verification set (dry-run only), from spec §1a: `20238` minimal ·
`20156`/`18620`/`18826` out-of-scope child rule · `20438`/`20441` exercise the
Faturamento path (a tracker-15 partner reached by relation, written to the
ProjectTask's Faturamento container).

**Container numbering is per-instance.** Production numbers the project
container **17** and the Faturamento container **18**; the test instance used 15
and 26, and on production container 15 belongs to an unrelated ProjectTask
container. Trust `config/settings.py`, never a number transcribed into prose.

---

## 10. Rules the deployment must not break

1. **Dry-run stays the default.** Never set `--apply`/`--yes` as a baked-in default,
   an env var, or a panel default.
2. **One process, one job.** No multi-worker, no replicas, no blue/green with both
   sides live (§6.2).
3. **No unauthenticated network exposure** of the panel (§6.1).
4. **Secrets only from env/`.env`.** Never in the image, the unit file's `Environment=`
   inline (use `EnvironmentFile=` with `0600`), the repo, or logs. New secrets must be
   registered via `messages.register_secrets()`.
5. **Do not disable TLS verification** for GLPI. Fix trust, not the client.
6. **Do not point a fresh deployment at a stale `migration.db`** from another host,
   and do not share one DB file between two instances — SQLite plus two writers is
   not part of this design.

---

## 11. Open items to settle with the operator before go-live

- **Which VM / OS**, and whether the panel is reached over an SSH tunnel (§6.1 option 1)
  or a proxy with auth (option 2). This is the one decision that changes the unit file.
- **Egress path to Redmine over plain HTTP** (§4) — acceptable on the target network?
- **`migration.db` location and backup**: shared volume vs. local, and whether the
  History tab is expected to survive redeploys.
- **Report retention** — where `report_*.txt` are archived, given they contain
  migrated business data (no credentials; they pass through `redact()`).
- Five project-container columns are listed as mandatory and the `plan_end_date` /
  `real_end_date` sources are still undecided (spec §11, summarised in `CLAUDE.md`).
- **`post_max_size` in the GLPI server's `php.ini`** — measured at roughly 16 MB,
  below what GLPI itself accepts. Attachments above it are reported as
  `FAILED_UPLOAD` and stay in Redmine. Raise it *before* a full run, not after.
  Raise `upload_max_filesize` with it; `post_max_size` must remain the larger.
- **789 production projects named "RDM &lt;n&gt;" carry no `rdmfield` marker**, so
  dedup cannot see them and a batch will migrate those roots again. Decide what
  happens to them before the run.

  These are *functional* open points, not deployment blockers, but they mean the tool
  is not yet signed off for unattended production use.

---

## 12. Pre-flight for the deploying agent

- [ ] Working tree clean (`git status`). Do not deploy from a dirty tree.
- [ ] `python -m pytest tests -q` green (322 tests) on the machine you package from.
- [ ] `wsgi.py` present — it **is** in the repo now, and reads `MIGRATION_DB_PATH`
      and `MIGRATION_REPORTS_DIR` from the environment.
- [ ] `MIGRATION_REPORTS_DIR` points somewhere **writable**. Both long-running
      workers probe it with a real file before doing any work and abort naming the
      variable if the probe fails; a read-only application directory is the
      failure this guards.
- [ ] `gunicorn` (or `waitress`) added to the deploy environment — not in
      `requirements.txt`.
- [ ] `.env` transferred out-of-band, mode `600`, correct owner.
- [ ] Python 3.11+ available on the VM (3.12 preferred).
- [ ] Egress to `172.178.61.88:80` and
      `smartsystems-apps.brazilsouth.cloudapp.azure.com:443` verified from the VM.
- [ ] DB directory exists and is writable by the service user.
- [ ] `MIGRATION_REPORTS_DIR` set to a path **outside** the application directory,
      the directory created and owned by the service user, and covered by
      `ReadWritePaths=`. Without it the Progresso and Lote views fail on EROFS.
- [ ] Panel port **not** opened in the VM firewall / NSG unless option 2 with auth
      is deliberately chosen.
- [ ] §9 steps 1–4 green.

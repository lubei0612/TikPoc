# Portfolio Demo Data Operations Runbook

## Scope And Fixed Paths

This runbook operates the synthetic dataset named
`demo-ai-growth-v1`. Every demo account, device, target, conversation, message,
and destination is synthetic and visibly prefixed with `demo-`, `demo_`, or
`DEMO`. The figures demonstrate product flow, metrics, and exception handling;
they are not measured production results.

Production paths inside the `tikpoc-admin` container are fixed:

```text
Database: /data/tikpoc.db
Web accounts: /data/config/web-accounts.demo.yaml
Runtime settings: /data/config/secrets/operator-settings.json
Backups: /data/backups
Namespace: demo-ai-growth-v1
```

The dashboard process starts **without** `--with-web-worker`. Keep standalone
browser/web-worker services stopped, and keep mobile worker services absent.
Preview, seed, clear, API reads, and browser reads do not perform TikTok UI
actions.

## Local Preview And Seed

Run from the repository root. Use a disposable directory so this rehearsal does
not touch an operator database or account configuration:

```bash
DEMO_DIR="$(mktemp -d /tmp/tikpoc-portfolio-demo.XXXXXX)"
DEMO_DB="$DEMO_DIR/tikpoc.db"
DEMO_ACCOUNTS="$DEMO_DIR/web-accounts.demo.yaml"
DEMO_SETTINGS="$DEMO_DIR/config/secrets/operator-settings.json"
DEMO_BACKUPS="$DEMO_DIR/backups"
DEMO_NOW_MS="$(($(date +%s) * 1000))"

uv run tikpoc demo-data preview \
  --db "$DEMO_DB" \
  --now-ms "$DEMO_NOW_MS" \
  --json | tee "$DEMO_DIR/preview.json"
uv run tikpoc demo-data seed \
  --db "$DEMO_DB" \
  --web-accounts "$DEMO_ACCOUNTS" \
  --settings "$DEMO_SETTINGS" \
  --backup-dir "$DEMO_BACKUPS" \
  --now-ms "$DEMO_NOW_MS" \
  --json | tee "$DEMO_DIR/seed.json"

jq '{namespace, pool_id, round_id, created_total, metrics, summary, backup_path}' \
  "$DEMO_DIR/seed.json"
find "$DEMO_BACKUPS" -maxdepth 1 -type f -exec stat -f '%Sp %N' {} \;
```

The seed summary must name `demo-ai-growth-v1`, 10,000 targets, seven accounts,
70,000 assignments, and 68,420 confirmed visits. Backup and provenance-sidecar
permissions must be `-rw-------` (`0600`). Running the identical seed command a
second time with the same `DEMO_NOW_MS` is an idempotency check: `created_total`
must be `0`, and `backup_path` must be unchanged.

Start the read-only console surface without a browser worker:

```bash
uv run tikpoc dashboard \
  --db "$DEMO_DB" \
  --web-accounts "$DEMO_ACCOUNTS" \
  --env-file /dev/null \
  --host 127.0.0.1 \
  --port 8765
```

The command intentionally omits `--with-web-worker`. In another terminal, run
the API and browser checks below with `ORIGIN=http://127.0.0.1:8765` and without
authentication arguments.

## Production Preview, Seed, And Worker Gate

Run these commands on the server. They affect only the
`/home/ubuntu/tikpoc-admin/compose.yml` project and its `/data` volume.

```bash
cd /home/ubuntu/tikpoc-admin
COMPOSE='docker compose -f /home/ubuntu/tikpoc-admin/compose.yml'

$COMPOSE ps
$COMPOSE top tikpoc-admin
$COMPOSE config | grep -F -- '--with-web-worker' && exit 1 || true
$COMPOSE ps --services --status running | grep -E '(^|[-_])(mobile|web)[-_]?worker($|[-_])' && exit 1 || true

$COMPOSE exec -T tikpoc-admin \
  tikpoc demo-data preview --db /data/tikpoc.db --json \
  | tee /tmp/tikpoc-demo-preview.json

$COMPOSE exec -T tikpoc-admin \
  tikpoc demo-data seed \
    --db /data/tikpoc.db \
    --web-accounts /data/config/web-accounts.demo.yaml \
    --settings /data/config/secrets/operator-settings.json \
    --backup-dir /data/backups \
    --json \
  | tee /tmp/tikpoc-demo-seed.json

jq '{namespace, pool_id, round_id, created_total, metrics, summary, backup_path}' \
  /tmp/tikpoc-demo-seed.json
BACKUP_PATH="$(jq -r '.backup_path' /tmp/tikpoc-demo-seed.json)"
$COMPOSE exec -T tikpoc-admin sh -c \
  'test "$(stat -c %a "$1")" = 600 && test "$(stat -c %a "${1%.db}.json")" = 600' \
  sh "$BACKUP_PATH"
```

Do not add `--with-web-worker` to the dashboard command or Compose definition.
Do not start `tikpoc web-worker`, `tikpoc fleet-run`, `tikpoc run`, an Android
worker unit, or a mobile worker container for this dataset.

## Health And API Evidence

For localhost checks inside the container:

```bash
cd /home/ubuntu/tikpoc-admin
COMPOSE='docker compose -f /home/ubuntu/tikpoc-admin/compose.yml'

$COMPOSE ps --status running tikpoc-admin
test "$(docker inspect --format '{{.State.Health.Status}}' \
  "$($COMPOSE ps -q tikpoc-admin)")" = healthy
$COMPOSE exec -T tikpoc-admin python - <<'PY'
import json
import urllib.request

origin = "http://127.0.0.1:8765"
paths = (
    "/api/status",
    "/api/runtime",
    "/api/rounds?offset=0&limit=100",
    "/api/operations?round_id=demo-round-ai-growth-v1",
    "/api/coverage?round_id=demo-round-ai-growth-v1&offset=0&limit=100",
    "/api/leads?limit=100",
    "/api/settings",
)
for path in paths:
    with urllib.request.urlopen(origin + path, timeout=15) as response:
        payload = json.load(response)
    print(path, json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
PY

$COMPOSE logs --since=5m --no-color tikpoc-admin
```

Confirm from the JSON, rather than HTTP status alone:

- `/api/rounds` contains the main demo round plus demo history;
- `/api/operations` contains seven devices and 68,420 confirmed visits;
- `/api/coverage` reports 10,000 total targets and 97.7% full coverage;
- `/api/leads` contains seven accounts, representative conversations, 14
  timeline days, and 19 synthetic sales;
- `/api/settings` contains seven accounts and reports the Provider key as not
  configured; no key or private destination appears in output.

For the protected public route, provide credentials only through local shell
variables so they do not enter this repository:

```bash
ORIGIN='https://tikpoc.tikpoc.site'
read -r -p 'Basic Auth user: ' BASIC_USER
read -r -s -p 'Basic Auth password: ' BASIC_PASSWORD; printf '\n'

test "$(curl -sS -o /dev/null -w '%{http_code}' "$ORIGIN/")" = 401
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -u "$BASIC_USER:$BASIC_PASSWORD" "$ORIGIN/api/status")" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -u "$BASIC_USER:$BASIC_PASSWORD" "$ORIGIN/api/runtime")" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -u "$BASIC_USER:$BASIC_PASSWORD" \
  "$ORIGIN/api/operations?round_id=demo-round-ai-growth-v1")" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -u "$BASIC_USER:$BASIC_PASSWORD" "$ORIGIN/api/leads?limit=100")" = 200
test "$(curl -sS -o /dev/null -w '%{http_code}' \
  -u "$BASIC_USER:$BASIC_PASSWORD" "$ORIGIN/api/settings")" = 200
HTTP_CODE="$(curl -sS -o /dev/null -w '%{http_code}' \
  'http://tikpoc.tikpoc.site/')"
test "$HTTP_CODE" = 301 || test "$HTTP_CODE" = 308
openssl s_client -connect tikpoc.tikpoc.site:443 \
  -servername tikpoc.tikpoc.site </dev/null 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

Unset the password after the checks:

```bash
unset BASIC_PASSWORD BASIC_USER
```

## Browser Acceptance

Open all four protected workspaces in the existing signed-in Chrome session:

```bash
open -a 'Google Chrome' 'https://tikpoc.tikpoc.site/operations'
open -a 'Google Chrome' 'https://tikpoc.tikpoc.site/inbox'
open -a 'Google Chrome' 'https://tikpoc.tikpoc.site/analytics'
open -a 'Google Chrome' 'https://tikpoc.tikpoc.site/settings'
```

At a desktop viewport, visibly verify the following and retain screenshots:

1. Operations: DEMO round selector, seven device rows, 97.7% coverage, timing
   evidence, quotas, and one explained warning.
2. Inbox: at least 20 conversations; open representative Chinese, English,
   invited, contact-captured, human-required, `sent`, `uncertain`, and
   `superseded` records.
3. Analytics: funnel, 19 sales, synthetic revenue, measured/projected labels,
   account contribution, AI handling summary, and all 14 timeline days.
4. Settings: seven accounts, synthetic business context, and Provider key shown
   as not configured.
5. Reload each route and repeat the visible totals check. In Chrome DevTools,
   verify that Console contains no application error and Network contains no
   failed API request.

Browser-visible state is the acceptance evidence. Successful API responses or
unit tests do not replace these checks.

## Clear Only The Demo Namespace

Preview the expected fixed dataset first, then clear it. Clear removes only the
demo namespace, deletes an all-demo web-account file, and removes demo accounts
from runtime settings. It retains backups and any unrelated runtime settings.

```bash
cd /home/ubuntu/tikpoc-admin
COMPOSE='docker compose -f /home/ubuntu/tikpoc-admin/compose.yml'

$COMPOSE exec -T tikpoc-admin \
  tikpoc demo-data preview --db /data/tikpoc.db --json
$COMPOSE exec -T tikpoc-admin \
  tikpoc demo-data clear \
    --db /data/tikpoc.db \
    --web-accounts /data/config/web-accounts.demo.yaml \
    --settings /data/config/secrets/operator-settings.json \
    --json \
  | tee /tmp/tikpoc-demo-clear.json
jq '{namespace, deleted_total, deleted}' /tmp/tikpoc-demo-clear.json
```

Repeat the health and API reads. The demo round and demo conversations must be
gone, unrelated records must remain, and no worker service may have started.

## Restore The Exact Pre-Seed Database

Use restore when the complete pre-seed database state is required instead of a
namespace-only clear. Select the `backup_path` from the saved seed summary and
keep its matching `.json` provenance sidecar. The restore command validates the
filename, namespace, SHA-256, permissions, and SQLite integrity before replacing
the database through SQLite's backup API.

```bash
cd /home/ubuntu/tikpoc-admin
COMPOSE='docker compose -f /home/ubuntu/tikpoc-admin/compose.yml'
BACKUP_PATH="$(jq -r '.backup_path' /tmp/tikpoc-demo-seed.json)"
test -n "$BACKUP_PATH" && test "$BACKUP_PATH" != null

$COMPOSE stop tikpoc-admin
$COMPOSE run --rm --no-deps -T --entrypoint python tikpoc-admin \
  - "$BACKUP_PATH" /data/tikpoc.db <<'PY'
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

backup = Path(sys.argv[1])
database = Path(sys.argv[2])
sidecar = backup.with_suffix(".json")
for path in (backup, sidecar):
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise SystemExit(f"insecure mode for {path}: {mode:o}")
provenance = json.loads(sidecar.read_text(encoding="utf-8"))
digest = hashlib.sha256(backup.read_bytes()).hexdigest()
if provenance.get("backup_filename") != backup.name:
    raise SystemExit("backup filename provenance mismatch")
if provenance.get("namespace") != "demo-ai-growth-v1":
    raise SystemExit("backup namespace provenance mismatch")
if provenance.get("sha256") != digest:
    raise SystemExit("backup digest provenance mismatch")
with sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True) as source:
    if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise SystemExit("backup quick_check failed")
    temporary = database.with_suffix(database.suffix + ".restore")
    temporary.unlink(missing_ok=True)
    with sqlite3.connect(temporary) as target:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SystemExit("restored quick_check failed")
    os.chmod(temporary, 0o600)
    for suffix in ("-journal", "-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    os.replace(temporary, database)
PY
$COMPOSE up -d tikpoc-admin
for attempt in $(seq 1 60); do
  STATUS="$(docker inspect --format '{{.State.Health.Status}}' \
    "$($COMPOSE ps -q tikpoc-admin)")"
  test "$STATUS" = healthy && break
  test "$attempt" = 60 && { echo "tikpoc-admin health=$STATUS" >&2; exit 1; }
  sleep 1
done
$COMPOSE logs --since=2m --no-color tikpoc-admin
```

Repeat `/api/status`, `/api/runtime`, `/api/rounds`, `/api/leads`, and
`/api/settings`, then reload all four browser routes. Confirm the pre-seed state
is restored and the dashboard command still omits `--with-web-worker`.

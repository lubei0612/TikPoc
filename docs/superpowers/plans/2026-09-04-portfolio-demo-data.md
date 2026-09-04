# TikPoc Portfolio Demo Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, verify, and deploy a deterministic synthetic dataset that makes all four TikPoc operator-console workspaces tell a credible AI product-management story without starting any live worker.

**Architecture:** Add a focused `demo_data` module that owns the synthetic namespace, deterministic fixture blueprint, transactional database writes, web-account configuration, runtime settings, preview, backup, and cleanup. Reuse existing acquisition and conversation repositories for domain behavior, use bounded bulk SQL only for the 70,000 assignment-state projection, and expose a derived 14-day timeline through the existing lead API for a lightweight React visualization.

**Tech Stack:** Python 3.12, SQLite, FastAPI, PyYAML, argparse, React 19, TypeScript, Vite, Vitest, Playwright, Docker Compose, Caddy.

---

## File Structure

- Create `src/tikpoc/demo_data.py`: deterministic blueprint, synthetic entity builders, seed/preview/clear orchestration, backup, account YAML and runtime-settings output.
- Create `tests/test_demo_data.py`: deterministic blueprint, transactional persistence, exact default counts, idempotency, cleanup isolation, and rollback tests.
- Modify `src/tikpoc/cli.py`: `demo-data preview|seed|clear` command group and JSON output.
- Modify `tests/test_cli.py`: CLI parsing, preview, seed, replay, and clear coverage.
- Modify `src/tikpoc/db.py`: account-scoped 14-day funnel timeline and AI/manual handling summary queries.
- Modify `tests/test_lead_conversion.py`: timeline aggregation and account filtering tests.
- Modify `src/tikpoc/api.py`: include `timeline` and `demo` metadata in `/api/leads`.
- Modify `tests/test_lead_api.py`: API serialization and secret-redaction assertions.
- Modify `operator-console/src/api.ts`: timeline and demo metadata types.
- Modify `operator-console/src/views/AnalyticsView.tsx`: 14-day AI conversion trend and explicit DEMO context.
- Modify `operator-console/src/InboxView.test.tsx`: analytics rendering tests using the expanded payload.
- Modify `operator-console/src/styles.css`: accessible timeline bars and demo badge.
- Regenerate `src/tikpoc/static/console/*`: production console assets from Vite.
- Create `docs/runbooks/portfolio-demo-data.md`: local and server seed, clear, restore, and evidence commands.
- Modify `AGENTS.md`: append the final verified portfolio-demo checkpoint after live acceptance.

## Fixed Demo Contract

The default blueprint uses these immutable identifiers and totals:

```python
DEMO_NAMESPACE = "demo-ai-growth-v1"
DEMO_POOL_ID = "demo-pool-ai-growth-v1"
DEMO_ROUND_ID = "demo-round-ai-growth-v1"
DEMO_POOL_IDENTITY_PREFIX = "demo:target:"
DEMO_ROUND_LABEL = "DEMO · AI 多账号获客转化试点"
DEMO_TARGETS = 10_000
DEMO_DEVICES = 7
DEMO_ASSIGNMENTS = 70_000
DEMO_CONFIRMED_VISITS = 68_420
DEMO_FULLY_COVERED = 9_770
DEMO_ELIGIBLE = 5_860
DEMO_INTERACTIONS = 4_410
DEMO_FOLLOWERS = 1_240
DEMO_INBOUND = 486
DEMO_ENGAGED = 326
DEMO_QUALIFIED = 173
DEMO_INVITED = 126
DEMO_CONTACT_CAPTURED = 72
DEMO_HUMAN_REQUIRED = 28
DEMO_SALES = 19
DEMO_AI_PLANS = 348
DEMO_AI_SENT = 331
DEMO_AI_UNCERTAIN = 5
DEMO_AI_SUPERSEDED = 12
```

`9,770 * 7 + 30 = 68,420`, so 9,770 targets receive complete `7/7` coverage and 30 additional confirmed visits are distributed across the remaining 230 targets. The UI coverage rate is therefore 97.7%, matching the repository's definition of `fully_covered / targets`.

---

### Task 1: Deterministic Demo Blueprint and Synthetic Account Files

**Files:**
- Create: `src/tikpoc/demo_data.py`
- Create: `tests/test_demo_data.py`

- [ ] **Step 1: Write the failing blueprint tests**

Add tests that assert the fixed contract, deterministic identifiers, seven unique account/device mappings, 14 daily buckets, and synthetic-only destinations:

```python
from tikpoc.demo_data import DemoScale, build_demo_blueprint


def test_default_blueprint_matches_portfolio_contract() -> None:
    blueprint = build_demo_blueprint(now_ms=1_788_499_200_000)

    assert blueprint.namespace == "demo-ai-growth-v1"
    assert len(blueprint.targets) == 10_000
    assert len(blueprint.accounts) == 7
    assert blueprint.metrics.confirmed_visits == 68_420
    assert blueprint.metrics.fully_covered == 9_770
    assert blueprint.metrics.sales == 19
    assert len(blueprint.timeline) == 14
    assert all(item.account_id.startswith("demo-account-") for item in blueprint.accounts)
    assert all("example" in item.private_channel_hint for item in blueprint.accounts)


def test_blueprint_is_deterministic_for_same_clock_and_scale() -> None:
    scale = DemoScale.portfolio()
    first = build_demo_blueprint(now_ms=1_788_499_200_000, scale=scale)
    second = build_demo_blueprint(now_ms=1_788_499_200_000, scale=scale)
    assert first == second
```

- [ ] **Step 2: Run the tests and observe the expected failure**

Run:

```bash
uv run pytest tests/test_demo_data.py -q
```

Expected: collection fails because `tikpoc.demo_data` does not exist.

- [ ] **Step 3: Implement immutable blueprint types and builders**

Create frozen dataclasses `DemoScale`, `DemoMetrics`, `DemoAccount`, `DemoConversation`, `DemoTimelineDay`, and `DemoBlueprint`. Implement `DemoScale.portfolio()` with the fixed contract and `DemoScale.test_fixture()` with 12 targets, 3 devices, 31 assignments confirmed, 8 conversations, and 3 sales. Use `random.Random(20260904)` locally inside the builder, and derive all timestamps from the injected `now_ms`.

The public entry point must be:

```python
def build_demo_blueprint(
    *,
    now_ms: int,
    scale: DemoScale | None = None,
) -> DemoBlueprint:
    selected = scale or DemoScale.portfolio()
    if now_ms <= 0:
        raise ValueError("demo clock must be positive")
    return _build_blueprint(selected, now_ms=now_ms, seed=20260904)
```

Account IDs must be `demo-account-01` through `demo-account-07`, device IDs `demo-device-01` through `demo-device-07`, profile labels `DEMO Profile 01` through `DEMO Profile 07`, and usernames `demo_shop_01` through `demo_shop_07`. Private-channel hints use `https://example.invalid/demo-channel/NN`.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
uv run pytest tests/test_demo_data.py -q
uv tool run ruff check src/tikpoc/demo_data.py tests/test_demo_data.py
uv tool run ruff format --check src/tikpoc/demo_data.py tests/test_demo_data.py
```

Expected: all focused tests and Ruff checks pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/tikpoc/demo_data.py tests/test_demo_data.py
git commit -m "feat: define deterministic portfolio demo data"
```

---

### Task 2: Transactional Acquisition and Capacity Seed

**Files:**
- Modify: `src/tikpoc/demo_data.py`
- Modify: `tests/test_demo_data.py`

- [ ] **Step 1: Write failing acquisition persistence tests**

Use `DemoScale.test_fixture()` for the database integration test and verify production totals through the pure blueprint test:

```python
def test_seed_acquisition_is_idempotent_and_matches_small_scale(tmp_path: Path) -> None:
    path = tmp_path / "tikpoc.db"
    blueprint = build_demo_blueprint(
        now_ms=1_788_499_200_000,
        scale=DemoScale.test_fixture(),
    )

    first = seed_demo_database(path, blueprint)
    second = seed_demo_database(path, blueprint)
    repository = AcquisitionRepository(path)

    assert first.created["targets"] == 12
    assert first.created["assignments"] == 36
    assert second.created_total == 0
    assert repository.assignment_count(blueprint.round_id) == 36
    coverage = repository.round_coverage(blueprint.round_id)
    assert coverage["confirmed_visits"] == 31
    assert coverage["required_devices"] == 3
```

Add a rollback test that monkeypatches `_seed_conversion` to raise and asserts no `demo:%` target, round, assignment, or health row remains.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
uv run pytest tests/test_demo_data.py -q
```

Expected: failures for missing `seed_demo_database` and `DemoSeedResult`.

- [ ] **Step 3: Implement acquisition seeding**

Implement `seed_demo_database(path, blueprint)` so it:

1. calls `AcquisitionRepository(path).migrate()` and `Database(path).migrate()`;
2. opens one SQLite connection with `PRAGMA foreign_keys=ON` and `BEGIN IMMEDIATE`;
3. inserts the deterministic target pool, targets, round, seven seeds, and assignments with `INSERT OR IGNORE`;
4. marks the exact deterministic subset as confirmed/completed using `executemany`;
5. inserts `profile_opening` phase history so device mean and P90 derive from stored evidence;
6. inserts four evenly distributed action outcomes and five deliberately uncertain plans;
7. inserts six healthy and one degraded `fleet_device_health` row;
8. leaves every operator control state `stopped` or `paused` and creates no worker lease;
9. commits only after conversion seeding and file generation succeed.

The bulk update helper must receive explicit rows rather than interpolate values:

```python
connection.executemany(
    """
    UPDATE round_assignments
    SET phase=?, attempt_count=?, visit_confirmed_at_ms=?, completed_at_ms=?,
        last_error_code=NULL
    WHERE round_id=? AND identity_key=? AND device_id=?
    """,
    assignment_updates,
)
```

- [ ] **Step 4: Verify acquisition summaries and query performance**

```bash
uv run pytest tests/test_demo_data.py -q
uv run python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from tikpoc.demo_data import build_demo_blueprint, seed_demo_database

with TemporaryDirectory() as directory:
    path = Path(directory) / "demo.db"
    blueprint = build_demo_blueprint(now_ms=1_788_499_200_000)
    started = perf_counter()
    result = seed_demo_database(path, blueprint)
    print(result.created["assignments"], round(perf_counter() - started, 2))
PY
```

Expected: 70,000 assignments are created; the command completes without errors and records elapsed time as measured evidence rather than a hard-coded acceptance claim.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/tikpoc/demo_data.py tests/test_demo_data.py
git commit -m "feat: seed portfolio acquisition evidence"
```

---

### Task 3: Conversation, AI Plan, Funnel, Sales, and Settings Seed

**Files:**
- Modify: `src/tikpoc/demo_data.py`
- Modify: `tests/test_demo_data.py`

- [ ] **Step 1: Write failing conversion and isolation tests**

Add tests that seed the small scale and assert all stages, message directions, plan states, sales, health rows, generated account YAML, empty Provider key, and cleanup isolation:

```python
def test_seed_conversion_populates_inbox_settings_and_sales(tmp_path: Path) -> None:
    path = tmp_path / "tikpoc.db"
    accounts_path = tmp_path / "web-accounts.yaml"
    settings_path = tmp_path / "config/secrets/operator-settings.json"
    blueprint = build_demo_blueprint(
        now_ms=1_788_499_200_000,
        scale=DemoScale.test_fixture(),
    )

    seed_demo_database(
        path,
        blueprint,
        web_accounts_path=accounts_path,
        runtime_settings_path=settings_path,
        backup_dir=tmp_path / "backups",
    )

    registry = WebAccountRegistry.from_path(accounts_path)
    database = Database(path)
    assert len(registry.accounts) == 3
    assert {row["stage"] for row in database.lead_conversations(
        account_ids=tuple(item.account_id for item in registry.accounts),
        limit=100,
        now_ms=blueprint.now_ms,
    )} >= {"qualified", "invited", "human_required", "closed"}
    assert database.lead_sales_snapshot()["sales"] == 3
    assert RuntimeSettingsStore(settings_path).provider_credentials().key_configured is False
```

Insert one unrelated non-demo conversation before seeding, run `clear_demo_database`, and assert that unrelated row remains.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
uv run pytest tests/test_demo_data.py -q
```

Expected: failures for missing conversion seed and clear behavior.

- [ ] **Step 3: Implement conversion data through existing domain methods**

Use these existing methods for domain-consistent rows:

```python
database.append_web_message(...)
database.reserve_browser_reply_plan(...)
database.complete_browser_reply_plan(...)
database.record_browser_reply_result(...)
database.record_lead_funnel_event(...)
database.record_lead_sale(...)
database.upsert_browser_health(...)
database.set_account_operator_setting(...)
database.create_manual_reply_plan(...)
RuntimeSettingsStore(settings_path).save_account(...)
```

Generate at least 20 detailed conversations and aggregate-only synthetic events for the remaining funnel counts. Detailed conversations must include Chinese and English purchasing questions, invitations, captured contacts using `demo@example.invalid`, human handoff, refund/complaint/cancellation, five uncertain plans, and twelve superseded plans. Do not create a claimed browser action lease.

In addition to the 348 AI-origin plans, create 98 synthetic manual-handling outcomes. Together with 331 confirmed AI sends and 28 human-required outcomes, this yields 457 handled inbound decisions and an AI automatic handling rate of `331 / 457 = 72.4%`; the remaining 29 of 486 inbound items remain awaiting classification. The automation summary must be computed from persisted rows rather than returned as an isolated display constant.

Write account YAML atomically with mode `browser`, automation switches disabled for execution, expected usernames, profile labels, synthetic Offer/FAQ content, and synthetic private-channel hints. Runtime settings must contain account context only; the `provider` object must be absent or have an empty `api_key`.

Add the private `create_database_backup(path, backup_dir, now_ms)` helper in this task and require `seed_demo_database` to receive a backup directory whenever configuration files are requested. Stage both configuration files under sibling temporary names before opening the database transaction. Preserve byte-for-byte copies of any existing configuration files. After the database commit, promote both temporary files with `os.replace`; if either promotion fails, restore the database from the pre-seed backup and restore both prior configuration files before returning an error. Always remove temporary files in `finally`, making the database and generated configuration a compensated all-or-nothing operation. Task 4 exposes this already-tested helper through the CLI.

- [ ] **Step 4: Implement namespace-scoped cleanup**

`clear_demo_database` must delete only rows reachable from the fixed demo pool/round, `demo-account-*`, `demo_lead_*`, and `demo:` source keys. Execute deletes in foreign-key-safe order inside `BEGIN IMMEDIATE`. Remove generated YAML only when all contained account IDs begin with `demo-account-`; remove demo account keys from runtime settings while preserving any non-demo provider and accounts.

- [ ] **Step 5: Run conversion, idempotency, cleanup, and rollback tests**

```bash
uv run pytest tests/test_demo_data.py -q
uv tool run ruff check src/tikpoc/demo_data.py tests/test_demo_data.py
uv tool run ruff format --check src/tikpoc/demo_data.py tests/test_demo_data.py
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/tikpoc/demo_data.py tests/test_demo_data.py
git commit -m "feat: seed synthetic AI conversion funnel"
```

---

### Task 4: Preview, Backup, Seed, and Clear CLI

**Files:**
- Modify: `src/tikpoc/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `src/tikpoc/demo_data.py`

- [ ] **Step 1: Write failing CLI tests**

Test `preview`, `seed`, replay, and `clear` by calling `main()` with a temporary database and paths. Assert JSON remains free of message text, contacts, and secrets:

```python
def test_demo_data_seed_replays_and_clear_preserves_backup(tmp_path, capsys) -> None:
    db = tmp_path / "tikpoc.db"
    accounts = tmp_path / "web-accounts.yaml"
    settings = tmp_path / "operator-settings.json"
    backup_dir = tmp_path / "backups"
    args = [
        "demo-data", "seed", "--db", str(db),
        "--web-accounts", str(accounts),
        "--settings", str(settings),
        "--backup-dir", str(backup_dir),
        "--now-ms", "1788499200000", "--json",
    ]

    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["namespace"] == "demo-ai-growth-v1"
    assert Path(first["backup_path"]).exists()
    assert main(args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["created_total"] == 0
    assert main(["demo-data", "clear", "--db", str(db), "--web-accounts", str(accounts), "--settings", str(settings), "--json"]) == 0
```

- [ ] **Step 2: Run the focused CLI tests and confirm failure**

```bash
uv run pytest tests/test_cli.py -q
```

Expected: argparse rejects `demo-data` before implementation.

- [ ] **Step 3: Add the command group**

Add `demo-data` with required subcommands:

```text
tikpoc demo-data preview --db /data/tikpoc.db --now-ms 1788499200000 --json
tikpoc demo-data seed --db /data/tikpoc.db --web-accounts /data/config/web-accounts.demo.yaml --settings /data/config/secrets/operator-settings.json --backup-dir /data/backups --now-ms 1788499200000 --json
tikpoc demo-data clear --db /data/tikpoc.db --web-accounts /data/config/web-accounts.demo.yaml --settings /data/config/secrets/operator-settings.json --json
```

`preview` performs no filesystem mutation. `seed` uses `sqlite3.Connection.backup()` to create `tikpoc-before-demo-YYYYMMDDTHHMMSSZ.db` before the first mutation and refuses to overwrite an existing backup. `clear` returns per-entity deletion counts. JSON includes IDs, metrics, counts, and backup path but excludes message bodies, private-channel strings, environment values, and API keys.

- [ ] **Step 4: Run CLI and demo tests**

```bash
uv run pytest tests/test_cli.py tests/test_demo_data.py -q
uv tool run ruff check src/tikpoc/cli.py src/tikpoc/demo_data.py tests/test_cli.py tests/test_demo_data.py
uv tool run ruff format --check src/tikpoc/cli.py src/tikpoc/demo_data.py tests/test_cli.py tests/test_demo_data.py
```

Expected: all focused tests and checks pass.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/tikpoc/cli.py src/tikpoc/demo_data.py tests/test_cli.py tests/test_demo_data.py
git commit -m "feat: add portfolio demo data CLI"
```

---

### Task 5: Fourteen-Day Funnel Timeline API and Console Visualization

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_lead_conversion.py`
- Modify: `src/tikpoc/api.py`
- Modify: `tests/test_lead_api.py`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/views/AnalyticsView.tsx`
- Modify: `operator-console/src/InboxView.test.tsx`
- Modify: `operator-console/src/styles.css`
- Regenerate: `src/tikpoc/static/console/*`

- [ ] **Step 1: Write failing database and API timeline tests**

Add `Database.lead_funnel_timeline(account_ids, start_ms, days)` tests with events on two dates and an excluded account. Add `Database.lead_automation_snapshot(account_ids)` tests covering AI sent, manual handled, human required, pending inbound, plan states, and the derived 72.4% rate. Assert exactly 14 ascending UTC day buckets and zero-filled missing days. Extend the lead API test to assert:

```python
assert payload["demo"] == {
    "active": True,
    "namespace": "demo-ai-growth-v1",
    "label": "DEMO · AI 多账号获客转化试点",
}
assert len(payload["timeline"]) == 14
assert payload["automation"] == {
    "ai_plans": 348,
    "ai_sent": 331,
    "ai_uncertain": 5,
    "ai_superseded": 12,
    "manual_handled": 98,
    "human_required": 28,
    "pending_inbound": 29,
    "automatic_handling_rate": 0.724,
}
assert set(payload["timeline"][0]) == {
    "date", "dm_inbound", "qualified", "invited", "contact_captured", "sales"
}
```

Demo metadata must be derived from configured `demo-account-*` accounts and fixed namespace rows; normal installations return `{"active": false}` and an empty timeline.

- [ ] **Step 2: Run Python tests and confirm failure**

```bash
uv run pytest tests/test_lead_conversion.py tests/test_lead_api.py -q
```

Expected: missing timeline method and payload keys.

- [ ] **Step 3: Implement account-scoped timeline aggregation**

Implement a read-only query that buckets `lead_funnel_events` and confirmed `lead_sales` by UTC day. Build all requested buckets in Python so dates without events remain visible. Add a separate automation aggregation over `browser_reply_plans`, `lead_funnel_events`, and inbound `web_messages`; count only configured account IDs, distinguish `plan_origin`, and calculate `automatic_handling_rate` as confirmed AI sends divided by AI-sent plus manual-handled plus human-required outcomes. Bind account IDs as parameters and preserve the existing 100-account cap.

- [ ] **Step 4: Extend `/api/leads` without exposing demo content**

Add `timeline`, `automation`, and `demo` to the existing response. Keep redaction behavior unchanged, and return aggregate counts only. Use the newest demo event timestamp as the 14-day window end so a portfolio snapshot remains stable after deployment.

- [ ] **Step 5: Write the failing React visualization test**

Expand `leadPayload` with `demo` and `timeline`, then assert:

```tsx
expect(await screen.findByText("DEMO · 合成演示数据")).toBeVisible();
expect(screen.getByRole("figure", { name: "14天 AI 转化趋势" })).toBeVisible();
expect(screen.getAllByTestId("timeline-day")).toHaveLength(14);
expect(screen.getByText("AI 自动处理率 72.4%", { exact: false })).toBeVisible();
```

- [ ] **Step 6: Run the frontend test and confirm failure**

```bash
cd operator-console
npm test -- --run src/InboxView.test.tsx
```

Expected: the DEMO badge and timeline figure are absent.

- [ ] **Step 7: Implement the accessible timeline**

Add a compact CSS-grid figure to `AnalyticsView` with 14 grouped bars for inbound, qualified, and captured contacts. Each day must have an `aria-label` containing date and all counts. Show the DEMO badge next to the workspace title only when `leads.demo.active` is true. Keep measured and projected labels unchanged.

- [ ] **Step 8: Verify and rebuild production assets**

```bash
cd operator-console
npm test
npm run build
npm run e2e
cd ..
uv run pytest tests/test_lead_conversion.py tests/test_lead_api.py tests/test_dashboard_static.py -q
git diff --check
```

Expected: frontend unit tests, Playwright, Python focused tests, and production build pass; generated files under `src/tikpoc/static/console/` match the build.

- [ ] **Step 9: Commit Task 5**

```bash
git add src/tikpoc/db.py src/tikpoc/api.py tests/test_lead_conversion.py tests/test_lead_api.py operator-console/src operator-console/dist src/tikpoc/static/console
git commit -m "feat: visualize portfolio AI conversion trend"
```

---

### Task 6: Runbook, Full Regression, and Independent Reviews

**Files:**
- Create: `docs/runbooks/portfolio-demo-data.md`
- Modify only if reviews find a concrete issue: files from Tasks 1-5

- [ ] **Step 1: Write the operational runbook**

Document exact preview, seed, clear, restore, health, API, and browser commands. Include the production paths:

```text
Database: /data/tikpoc.db
Web accounts: /data/config/web-accounts.demo.yaml
Runtime settings: /data/config/secrets/operator-settings.json
Backups: /data/backups
Namespace: demo-ai-growth-v1
```

State explicitly that `dashboard` starts without `--with-web-worker`, mobile worker services remain absent, and demo values are synthetic.

- [ ] **Step 2: Run the full applicable regression**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
node --test chrome-event-bridge/*.test.js
bash android-event-bridge/build.sh
cd operator-console && npm test && npm run build && npm run e2e && cd ..
git diff --check
git status --short --branch
```

Expected: every command exits zero. Record exact pass counts and durations; do not reuse earlier counts.

- [ ] **Step 3: Run an independent specification review**

Review the implementation against `docs/superpowers/specs/2026-09-04-portfolio-demo-data-design.md`. The reviewer must inspect the diff and verify deterministic generation, exact metrics, synthetic namespace, idempotency, cleanup isolation, rollback, disabled workers, API consistency, four populated views, and deployment/restore instructions.

- [ ] **Step 4: Fix every specification gap with a red-green cycle**

For each finding: add the smallest failing test, run it and capture the failure, implement the fix, rerun focused tests, then repeat the specification review until it passes.

- [ ] **Step 5: Run an independent code-quality review**

Review transaction boundaries, SQL parameterization, cleanup scope, performance, secret redaction, file permissions, clock determinism, type consistency, frontend accessibility, and maintainability. Fix every Critical or Important finding with a red-green cycle and repeat the review.

- [ ] **Step 6: Commit the accepted runbook and review fixes**

```bash
git add docs/runbooks/portfolio-demo-data.md src tests operator-console
git commit -m "docs: add portfolio demo operations runbook"
```

---

### Task 7: Deploy, Seed, Restart, and Perform Live Browser Acceptance

**Files:**
- Remote deployment: `/home/ubuntu/tikpoc-admin/repo`
- Remote deployment runner: `/home/ubuntu/tikpoc-admin/repo/deploy_server.py`
- Remote Compose: `/home/ubuntu/tikpoc-admin/compose.yml`
- Remote data volume paths: `/data/*`
- Modify after verification: `AGENTS.md`

- [ ] **Step 1: Re-read remote state before mutation**

Verify SSH host key, Git commit, dirty state, running containers, volume mount, available disk, Caddy route, and that no TikPoc mobile or web worker process is running. Preserve the existing remote `.env`, Caddy config, NexaPanel containers, and quantitative services.

- [ ] **Step 2: Push the reviewed branch and deploy the exact commit**

Confirm `git remote -v`, push `feat/web-lead-conversion`, fetch the exact reviewed commit on the server, rebuild only `tikpoc-admin`, and update `deploy_server.py` to load:

```python
registry = WebAccountRegistry.from_path(
    Path(os.environ["TIKPOC_WEB_ACCOUNTS"])
)
app = create_app(
    Path(os.environ["TIKPOC_DB"]),
    registry=registry,
    mobile_bootstrap_token=os.environ["TIKPOC_MOBILE_BOOTSTRAP_TOKEN"],
    live_batch_token=os.environ["TIKPOC_LIVE_BATCH_TOKEN"],
)
```

Set `TIKPOC_WEB_ACCOUNTS=/data/config/web-accounts.demo.yaml` in the ignored remote environment file. Keep `--with-web-worker` absent.

- [ ] **Step 3: Preview and back up before seeding**

Inside the new image, run:

```bash
tikpoc demo-data preview --db /data/tikpoc.db --json
tikpoc demo-data seed \
  --db /data/tikpoc.db \
  --web-accounts /data/config/web-accounts.demo.yaml \
  --settings /data/config/secrets/operator-settings.json \
  --backup-dir /data/backups \
  --json
```

Save the redacted JSON summary and confirm the backup exists with mode `0600` or stricter.

- [ ] **Step 4: Restart and verify durable server state**

Restart only `tikpoc-admin`, wait for `healthy`, and verify:

```text
GET /api/rounds -> demo round present
GET /api/operations?round_id=demo-round-ai-growth-v1 -> 7 devices, 68,420 visits
GET /api/coverage?round_id=demo-round-ai-growth-v1 -> 10,000 targets
GET /api/leads -> 7 accounts, conversations, funnel, 14 days, 19 sales
GET /api/settings -> 7 accounts, provider key not configured
```

Also confirm unauthenticated HTTPS remains `401`, HTTP redirects to HTTPS, the certificate matches `tikpoc.tikpoc.site`, and NexaPanel health remains unchanged.

- [ ] **Step 5: Perform real Chrome acceptance**

Using `https://tikpoc.tikpoc.site`, verify visible state for:

1. round selector and DEMO label;
2. seven device rows, 97.7% coverage, timings, quotas, and one warning;
3. at least 20 inbox conversations and representative detail drawers;
4. funnel, sales, revenue, capacity labels, and 14-day timeline;
5. seven automation accounts and empty Provider key;
6. page refresh persistence;
7. console errors and failed network requests;
8. desktop screenshots suitable for the portfolio.

- [ ] **Step 6: Update the project checkpoint**

Append an `AGENTS.md` checkpoint containing the deployed commit, synthetic namespace, backup path without secrets, test counts, browser acceptance results, container health, and the explicit statement that mobile and browser workers remained stopped.

- [ ] **Step 7: Commit the checkpoint**

```bash
git add AGENTS.md
git commit -m "docs: record portfolio demo deployment"
```

---

## Final Deliverables

After live acceptance, provide the user with:

1. the production URL and existing Basic Auth username;
2. a redacted deployment checkpoint;
3. the recommended screenshot order: overview, device evidence, inbox conversation, funnel/timeline, automation settings;
4. one long AI product-manager project description;
5. one concise résumé bullet group;
6. short captions for each screenshot;
7. a disclosure sentence: `页面数据为合成演示数据，用于展示产品流程、指标体系与异常治理机制。`;
8. clear and restore commands from the runbook.

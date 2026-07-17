# TikPoc Operator Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the compact legacy dashboard with a local operational console for target pools, exposure rounds, seven devices, quotas, retries, Chrome health, AI conversations, human takeover, funnel outcomes, and capacity evidence.

**Architecture:** A FastAPI application becomes the localhost HTTP owner while preserving browser-extension route contracts and the single SQLite state source. A React/TypeScript client consumes typed JSON snapshots and command endpoints. Read models are server-composed; UI controls submit idempotency keys and never mutate runtime state optimistically.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Pydantic, SQLite, pytest, React 19, TypeScript, Vite, Lucide React, Vitest, Testing Library, Playwright.

---

## File Map

- Modify `pyproject.toml`: FastAPI/Uvicorn runtime and HTTP test dependencies.
- Create `src/tikpoc/api.py`: application factory, dependencies, exception mapping, and static fallback.
- Create `src/tikpoc/api_models.py`: bounded response and request models.
- Create `src/tikpoc/acquisition_service.py`: transactional operator commands and dashboard read model.
- Modify `src/tikpoc/dashboard.py`: temporary compatibility wrapper and final Uvicorn server adapter.
- Modify `src/tikpoc/cli.py`: `serve` command points to the FastAPI app.
- Modify `tests/test_dashboard_api.py`: preserved legacy and browser route contracts.
- Create `tests/test_acquisition_api.py`: pool, round, fleet, quota, retry, and capacity endpoints.
- Create `tests/test_lead_api.py`: conversations, AI enable state, takeover, and sale endpoints.
- Create `operator-console/package.json`, `tsconfig.json`, `vite.config.ts`, and source files.
- Create `operator-console/src/api.ts`: typed API client and error model.
- Create `operator-console/src/App.tsx`: navigation shell and shared runtime state.
- Create `operator-console/src/views/OperationsView.tsx`: round, device, quota, coverage, and retry controls.
- Create `operator-console/src/views/InboxView.tsx`: conversations, drafts, contact capture, and manual takeover.
- Create `operator-console/src/views/AnalyticsView.tsx`: measured capacity and funnel reporting.
- Create `operator-console/src/components/*`: focused tables, status strips, dialogs, drawers, and icon controls.
- Create `operator-console/src/styles.css`: responsive operational visual system.
- Create `operator-console/src/*.test.tsx`: component and workflow tests.
- Create `tests/test_console_static.py`: built asset and fallback route tests.
- Create `tests/e2e/operator-console.spec.ts`: desktop/mobile Playwright acceptance.
- Modify `pyproject.toml` package-data rules to include built console assets.
- Create `docs/operator-console-runbook.md`: startup, controls, troubleshooting, and human takeover.

### Task 1: FastAPI Application Without Route Regressions

**Files:**
- Modify: `pyproject.toml`
- Create: `src/tikpoc/api.py`
- Create: `src/tikpoc/api_models.py`
- Modify: `tests/test_dashboard_api.py`

- [ ] **Step 1: Add failing compatibility tests**

```python
def test_fastapi_status_preserves_existing_snapshot(tmp_path: Path) -> None:
    app = create_app(tmp_path / "tikpoc.db")
    response = TestClient(app).get("/api/status")
    assert response.status_code == 200
    assert set(response.json()) >= {"control", "counts", "latest_event"}


def test_browser_origin_receives_exact_cors_headers(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "tikpoc.db", registry=browser_registry()))
    response = client.options(
        "/api/browser-events",
        headers={"Origin": "https://www.tiktok.com", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://www.tiktok.com"
```

- [ ] **Step 2: Run the dashboard API tests**

Run: `uv run pytest tests/test_dashboard_api.py -q`

Expected: FAIL because `create_app` does not exist.

- [ ] **Step 3: Add bounded dependencies**

```toml
dependencies = [
  "Appium-Python-Client>=5,<6",
  "fastapi>=0.116,<1",
  "openpyxl>=3.1,<4",
  "PyYAML>=6,<7",
  "uvicorn>=0.35,<1",
]

[project.optional-dependencies]
test = ["httpx>=0.28,<1", "pytest>=8,<9"]
```

Run `uv lock` and commit the resulting lock update when the repository contains
`uv.lock`.

- [ ] **Step 4: Implement the application factory**

```python
def create_app(
    database_path: Path,
    *,
    registry: WebAccountRegistry | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    database = Database(database_path)
    database.migrate()
    acquisition = AcquisitionRepository(database_path)
    acquisition.migrate()
    app = FastAPI(title="TikPoc Operator API", docs_url=None, redoc_url=None)
    app.state.database = database
    app.state.acquisition = acquisition
    app.state.registry = registry
    app.state.clock = clock
    register_legacy_routes(app)
    return app
```

Use explicit origin checks for TikTok browser routes rather than global wildcard
CORS. Keep business webhook signature verification and all existing status,
recent, control, browser-event, and device-event response shapes.

- [ ] **Step 5: Run API regression**

Run: `uv run pytest tests/test_dashboard_api.py tests/test_webhooks.py tests/test_web_events_db.py -q`

Expected: PASS for both the new app and retained compatibility wrapper tests.

- [ ] **Step 6: Commit the API foundation**

```bash
git add pyproject.toml uv.lock src/tikpoc/api.py src/tikpoc/api_models.py tests/test_dashboard_api.py
git commit -m "feat: add FastAPI operator service foundation"
```

### Task 2: Acquisition Read Models And Idempotent Controls

**Files:**
- Create: `src/tikpoc/acquisition_service.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/api_models.py`
- Create: `tests/test_acquisition_api.py`

- [ ] **Step 1: Write failing operations snapshot tests**

```python
def test_operations_snapshot_contains_round_device_quota_and_coverage(tmp_path: Path) -> None:
    app, round_id = seeded_operations_app(tmp_path)
    payload = TestClient(app).get(f"/api/operations?round_id={round_id}").json()
    assert payload["round"]["target_count"] == 2
    assert payload["coverage"]["required_devices"] == 2
    assert payload["devices"][0].keys() >= {"device_id", "health", "current_assignment", "mean_ms", "p90_ms"}
    assert payload["quotas"][0].keys() >= {"device_id", "outcome", "limit", "reserved", "confirmed", "remaining", "resets_at_ms"}


def test_repeated_pause_command_is_idempotent(tmp_path: Path) -> None:
    app, round_id = seeded_operations_app(tmp_path)
    client = TestClient(app)
    body = {"command_id": "command-1", "scope": "round", "scope_id": round_id}
    first = client.post("/api/commands/pause", json=body)
    second = client.post("/api/commands/pause", json=body)
    assert first.status_code == 200
    assert second.json() == first.json()
```

- [ ] **Step 2: Run acquisition API tests**

Run: `uv run pytest tests/test_acquisition_api.py -q`

Expected: FAIL because operations routes and command persistence are absent.

- [ ] **Step 3: Define bounded API models**

```python
class OperatorCommand(BaseModel):
    command_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    scope: Literal["fleet", "round", "device", "assignment"]
    scope_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RetryCommand(BaseModel):
    command_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    assignment_id: int = Field(gt=0)
```

Bound list limits to 1-500 and reject unknown command scopes.

- [ ] **Step 4: Persist commands and compose one read snapshot**

Add `operator_commands` unique by `command_id`. In one transaction, insert the
command, validate the current state, apply its state transition, and persist the
result JSON. A repeated command returns that exact stored result. The read model
joins acquisition assignments, fleet health, quota windows, capacity metrics,
browser heartbeat, and the latest diagnostic without issuing per-row queries.

- [ ] **Step 5: Add exact routes**

```text
GET  /api/pools
POST /api/pools/import
GET  /api/rounds
POST /api/rounds
GET  /api/operations?round_id=ROUND
GET  /api/coverage?round_id=ROUND&offset=0&limit=100
POST /api/commands/start
POST /api/commands/pause
POST /api/commands/stop
POST /api/commands/retry
GET  /api/diagnostics/ASSIGNMENT_ID
```

The import endpoint accepts a local path under configured import roots, rather
than uploading an arbitrary file body. Diagnostic paths are returned as opaque
IDs and served only through a bounded localhost endpoint.

- [ ] **Step 6: Run acquisition API and repository tests**

Run: `uv run pytest tests/test_acquisition_api.py tests/test_acquisition_db.py tests/test_capacity.py -q`

Expected: PASS.

- [ ] **Step 7: Commit operational APIs**

```bash
git add src/tikpoc/acquisition_service.py src/tikpoc/api.py src/tikpoc/api_models.py tests/test_acquisition_api.py
git commit -m "feat: expose idempotent acquisition operations"
```

### Task 3: Lead Inbox, AI Readiness, And Human Takeover APIs

**Files:**
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/db.py`
- Create: `tests/test_lead_api.py`

- [ ] **Step 1: Write failing lead workflow tests**

```python
def test_lead_list_redacts_destination_and_returns_readiness(tmp_path: Path) -> None:
    app = seeded_lead_app(tmp_path, configured_destination="WhatsApp: +1 555 0100")
    payload = TestClient(app).get("/api/leads?limit=20").json()
    assert payload["accounts"][0]["private_channel_configured"] is True
    assert "555 0100" not in json.dumps(payload)
    assert payload["conversations"][0].keys() >= {"stage", "participant_username", "last_message_preview", "human_required"}


def test_manual_takeover_disables_future_ai_plans(tmp_path: Path) -> None:
    app, account_id, conversation_id = seeded_ai_conversation_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        f"/api/leads/{account_id}/{conversation_id}/takeover",
        json={"command_id": "takeover-1", "reason": "operator"},
    )
    assert response.status_code == 200
    assert response.json()["stage"] == "human_required"
    assert next_reply_plan(client, account_id, conversation_id).status_code == 409
```

- [ ] **Step 2: Run lead API tests**

Run: `uv run pytest tests/test_lead_api.py -q`

Expected: FAIL because lead API routes and takeover command are absent.

- [ ] **Step 3: Implement bounded lead queries**

Return account readiness, funnel totals, conversation summaries, and a selected
conversation's bounded message history. Message previews are limited to 160
characters. Full private-channel destinations and model credentials never appear
in status responses. The operator may request a draft preview only for the
selected inbound fingerprint.

- [ ] **Step 4: Add idempotent lead commands**

```text
POST /api/leads/{account_id}/{conversation_id}/takeover
POST /api/leads/{account_id}/{conversation_id}/return-to-ai
POST /api/leads/{account_id}/{conversation_id}/manual-reply-plan
POST /api/leads/{account_id}/{conversation_id}/sale
POST /api/accounts/{account_id}/ai-enable
POST /api/accounts/{account_id}/followback-enable
```

Return-to-AI is accepted only before `contact_captured`, `human_required`, or
`closed` terminal policy state and only when no uncertain browser send exists.
Manual replies use the same immutable plan and action-lease path as AI replies.

- [ ] **Step 5: Run lead and browser regressions**

Run: `uv run pytest tests/test_lead_api.py tests/test_lead_conversion.py tests/test_web_events_db.py tests/test_dashboard_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit lead operations**

```bash
git add src/tikpoc/api.py src/tikpoc/api_models.py src/tikpoc/db.py tests/test_lead_api.py
git commit -m "feat: add lead inbox and human takeover APIs"
```

### Task 4: React Operations Workspace

**Files:**
- Create: `operator-console/package.json`
- Create: `operator-console/tsconfig.json`
- Create: `operator-console/vite.config.ts`
- Create: `operator-console/index.html`
- Create: `operator-console/src/main.tsx`
- Create: `operator-console/src/App.tsx`
- Create: `operator-console/src/api.ts`
- Create: `operator-console/src/styles.css`
- Create: `operator-console/src/views/OperationsView.tsx`
- Create: `operator-console/src/components/DeviceTable.tsx`
- Create: `operator-console/src/components/QuotaTable.tsx`
- Create: `operator-console/src/components/CoverageTable.tsx`
- Create: `operator-console/src/components/CommandBar.tsx`
- Create: `operator-console/src/OperationsView.test.tsx`

- [ ] **Step 1: Scaffold pinned frontend dependencies**

```json
{
  "private": true,
  "scripts": {
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "dev": "vite --host 127.0.0.1"
  },
  "dependencies": {
    "lucide-react": "0.468.0",
    "react": "19.0.0",
    "react-dom": "19.0.0"
  },
  "devDependencies": {
    "@testing-library/jest-dom": "6.6.3",
    "@testing-library/react": "16.1.0",
    "@types/react": "19.0.8",
    "@types/react-dom": "19.0.3",
    "@vitejs/plugin-react": "4.3.4",
    "typescript": "5.7.3",
    "vite": "6.1.0",
    "vitest": "3.0.5"
  }
}
```

After installation, commit the exact transitive resolution from
`package-lock.json`.

- [ ] **Step 2: Write the failing operations workflow test**

```tsx
it("pauses one device and refreshes only after server confirmation", async () => {
  mockOperations(twoHealthyDevices())
  render(<OperationsView roundId="round-1" />)
  await screen.findByText("phone-01")
  await user.click(screen.getByRole("button", { name: "Pause phone-01" }))
  expect(await screen.findByText("Paused")).toBeVisible()
  expect(commandRequests()).toEqual([{ scope: "device", scope_id: "phone-01" }])
})
```

- [ ] **Step 3: Build the quiet operational shell**

Use a compact top bar with product name, active round selector, fleet health,
and three tabs: Operations, Inbox, Analytics. Operations uses unframed page
bands and semantic tables, not nested cards. Provide icon buttons with Lucide
icons and tooltips for refresh, start, pause, stop, retry, diagnostics, and
screenshot. Keep card radii at 6 px and stable table/button dimensions.

- [ ] **Step 4: Implement typed fetch and command state**

`api.ts` exposes `getOperations`, `getCoverage`, and `postCommand`. Generate one
`crypto.randomUUID()` command ID per user action and reuse it on network retry.
Show pending state on only the affected command. Render server errors beside the
affected row and retain the last confirmed snapshot.

- [ ] **Step 5: Implement responsive operations views**

Desktop shows device, quota, and coverage tables with sticky headers. Under 720
px, device rows become full-width key/value bands and the coverage matrix uses
horizontal scrolling with a sticky target identity column. Text wraps within
controls; no viewport-scaled font sizes are used.

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
npm test --prefix operator-console
npm run build --prefix operator-console
```

Expected: tests pass and Vite writes the configured build directory.

- [ ] **Step 7: Commit the operations workspace**

```bash
git add operator-console
git commit -m "feat: build fleet operations console"
```

### Task 5: Inbox, Analytics, And Manual Workflow UI

**Files:**
- Create: `operator-console/src/views/InboxView.tsx`
- Create: `operator-console/src/views/AnalyticsView.tsx`
- Create: `operator-console/src/components/ConversationList.tsx`
- Create: `operator-console/src/components/ConversationDrawer.tsx`
- Create: `operator-console/src/components/FunnelTable.tsx`
- Create: `operator-console/src/InboxView.test.tsx`
- Modify: `operator-console/src/App.tsx`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/styles.css`

- [ ] **Step 1: Write failing takeover and capacity tests**

```tsx
it("takes over a conversation before enabling the manual composer", async () => {
  mockLeadInbox(qualifiedConversation())
  render(<InboxView />)
  await user.click(await screen.findByText("buyer_01"))
  expect(screen.getByRole("textbox", { name: "Manual reply" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "Take over" }))
  expect(await screen.findByRole("textbox", { name: "Manual reply" })).toBeEnabled()
})


it("labels measured capacity separately from projection", async () => {
  mockAnalytics({ measured: 652, projected: 10120, passed: false })
  render(<AnalyticsView />)
  expect(await screen.findByText("Measured completions")).toBeVisible()
  expect(screen.getByText("Projected daily capacity")).toBeVisible()
  expect(screen.getByText("Not promoted")).toBeVisible()
})
```

- [ ] **Step 2: Implement the inbox workspace**

Use a scannable conversation list and one side drawer for a selected thread;
avoid placing the thread inside another card. Show account, stage, last message,
reply latency, invitation/contact flags, and human state. The drawer shows
bounded messages, immutable pending draft, send state, takeover command, and
manual composer. Private destination readiness is boolean status only.

- [ ] **Step 3: Implement analytics**

Render compact tables for 7/7 coverage, device mean/p90, slowest-device daily
projection, followers, inbound DMs, qualified leads, invitations, captured
contacts, takeovers, sales, revenue, and revenue per 1,000 fully covered targets.
Use red/amber/green status colors with text labels; color is never the sole
indicator.

- [ ] **Step 4: Run frontend regression**

Run: `npm test --prefix operator-console && npm run build --prefix operator-console`

Expected: PASS.

- [ ] **Step 5: Commit inbox and analytics**

```bash
git add operator-console/src
git commit -m "feat: add lead inbox and acquisition analytics"
```

### Task 6: Static Integration And Browser Acceptance

**Files:**
- Modify: `operator-console/vite.config.ts`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/dashboard.py`
- Modify: `src/tikpoc/cli.py`
- Modify: `pyproject.toml`
- Create: `tests/test_console_static.py`
- Create: `tests/e2e/operator-console.spec.ts`
- Create: `docs/operator-console-runbook.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Write failing static fallback tests**

```python
def test_console_index_and_assets_are_served(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "tikpoc.db"))
    response = client.get("/operations")
    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    asset = re.search(r'src="([^"]+\.js)"', response.text).group(1)
    assert client.get(asset).status_code == 200
```

- [ ] **Step 2: Configure reproducible embedded build output**

Set Vite output to `src/tikpoc/static/console`, clear it on build, and use
`/console-assets/` as the asset base. Include `static/console/**/*` in package
data. FastAPI serves hashed assets with long cache headers and returns
`index.html` without cache for `/`, `/operations`, `/inbox`, and `/analytics`.

- [ ] **Step 3: Switch runtime serving to Uvicorn**

Keep `create_server` as a test compatibility adapter through this task. Change
the production `serve` command to `uvicorn.run(create_app(...), host="127.0.0.1", port=PORT)`. Confirm browser-extension endpoints retain the same localhost URL.

- [ ] **Step 4: Run Playwright desktop and mobile workflows**

At `1440x1000` and `390x844`, verify operations, inbox, and analytics load; table
headers, controls, and long identities do not overlap; command dialogs fit;
horizontal coverage scrolling retains the target column; and no console errors
occur. Capture screenshots to ignored test output.

Run: `npx playwright test tests/e2e/operator-console.spec.ts`

Expected: PASS at both viewports.

- [ ] **Step 5: Run complete verification**

```bash
uv run pytest -q
uv tool run ruff check src tests
uv tool run ruff format --check src tests
npm test --prefix operator-console
npm run build --prefix operator-console
node --test chrome-event-bridge/*.test.js
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 6: Document and commit the accepted console**

Document startup, account readiness, import/round controls, quota reading,
retry/diagnostics, manual takeover, sale recording, and measured-versus-projected
capacity. Update `AGENTS.md` with the latest verified state.

```bash
git add pyproject.toml uv.lock src/tikpoc operator-console tests/test_console_static.py tests/e2e docs/operator-console-runbook.md AGENTS.md
git commit -m "feat: deliver TikPoc operator console"
```

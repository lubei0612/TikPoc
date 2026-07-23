# Follow-back Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a durable account-scoped follow-back cooldown and one-action canary that protects restricted TikTok accounts without stopping AI messaging or read-only Activity monitoring.

**Architecture:** SQLite owns circuit transitions and atomically combines canary reservation with the existing follow-back lease. FastAPI exposes the effective circuit in account read models and provides an idempotent operator cooldown command. The Chrome Activity bridge adds a bounded reason to uncertain claimed follow results; DM and welcome behavior remain unchanged.

**Tech Stack:** Python 3.14, SQLite, FastAPI, Pydantic, Chrome Manifest V3 JavaScript, Node test runner, React/Vitest.

---

### Task 1: Durable Circuit State and Atomic Canary Claims

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_web_events_db.py`

- [ ] **Step 1: Write failing persistence and transition tests**

Add tests covering account isolation, expiry promotion, one canary claim, completed close, and uncertain reopen:

```python
def test_followback_circuit_cooldown_promotes_to_one_canary(tmp_path: Path) -> None:
    database = Database(tmp_path / "tikpoc.db")
    database.initialize()
    database.open_browser_action_circuit(
        "account-01", "followback", reason="platform_follow_reverted",
        opened_at_ms=1_000, cooldown_until_ms=2_000,
    )

    assert database.browser_action_circuit(
        "account-01", "followback", now_ms=1_999,
    )["state"] == "cooldown"
    assert database.claim_browser_followback_action(
        "account-01", "follow:one", "owner-01", 2_000, 30,
    )
    assert not database.claim_browser_followback_action(
        "account-01", "follow:two", "owner-02", 2_001, 30,
    )
    assert database.browser_action_circuit(
        "account-02", "followback", now_ms=2_001,
    )["state"] == "closed"
```

```python
def test_followback_canary_result_closes_or_reopens_circuit(tmp_path: Path) -> None:
    database = Database(tmp_path / "tikpoc.db")
    database.initialize()
    database.open_browser_action_circuit(
        "account-01", "followback", reason="platform_follow_reverted",
        opened_at_ms=1_000, cooldown_until_ms=2_000,
    )
    assert database.claim_browser_followback_action(
        "account-01", "follow:canary", "owner-01", 2_000, 30,
    )
    assert database.finish_browser_followback_action(
        "account-01", "follow:canary", "owner-01", "completed",
        reason="", now_ms=2_100,
    )
    assert database.browser_action_circuit(
        "account-01", "followback", now_ms=2_100,
    )["state"] == "closed"

    database.open_browser_action_circuit(
        "account-01", "followback", reason="platform_follow_reverted",
        opened_at_ms=3_000, cooldown_until_ms=4_000,
    )
    assert database.claim_browser_followback_action(
        "account-01", "follow:second-canary", "owner-02", 4_000, 30,
    )
    assert database.finish_browser_followback_action(
        "account-01", "follow:second-canary", "owner-02", "uncertain",
        reason="followback_unresolved", now_ms=4_100,
    )
    circuit = database.browser_action_circuit(
        "account-01", "followback", now_ms=4_100,
    )
    assert circuit["state"] == "cooldown"
    assert circuit["cooldown_until_ms"] == 4_100 + 86_400_000
```

- [ ] **Step 2: Run the tests and observe RED**

Run:

```bash
uv run pytest \
  tests/test_web_events_db.py::test_followback_circuit_cooldown_promotes_to_one_canary \
  tests/test_web_events_db.py::test_followback_canary_result_closes_or_reopens_circuit -q
```

Expected: both tests fail because the circuit table and methods do not exist.

- [ ] **Step 3: Implement the circuit table and database methods**

Add the schema from the design and implement:

```python
FOLLOWBACK_COOLDOWN_MS = 86_400_000

def browser_action_circuit(
    self, account_id: str, action_type: str, *, now_ms: int
) -> dict[str, object]: ...

def open_browser_action_circuit(
    self, account_id: str, action_type: str, *, reason: str,
    opened_at_ms: int, cooldown_until_ms: int,
) -> dict[str, object]: ...

def claim_browser_followback_action(
    self, account_id: str, action_key: str, owner_id: str,
    now_ms: int, lease_seconds: int,
) -> bool: ...

def finish_browser_followback_action(
    self, account_id: str, action_key: str, owner_id: str, state: str,
    *, reason: str, now_ms: int,
) -> bool: ...
```

Use `BEGIN IMMEDIATE`. Promote expired `cooldown` to `canary`; reserve
`canary_action_key` before inserting the lease; close on completed canary; reopen
for `FOLLOWBACK_COOLDOWN_MS` on uncertain canary. Validate state, action type,
reason length, timestamps, and identities.

- [ ] **Step 4: Run focused and database regression tests**

Run:

```bash
uv run pytest tests/test_web_events_db.py -q
```

Expected: all database tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/tikpoc/db.py tests/test_web_events_db.py
git commit -m "feat: add account followback circuit state"
```

### Task 2: API Claim Gate and Idempotent Cooldown Command

**Files:**
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_lead_api.py`

- [ ] **Step 1: Write failing API tests**

Add tests proving the operator command is idempotent, claim is blocked during
cooldown, one canary is admitted after expiry, and AI remains enabled:

```python
def test_followback_cooldown_blocks_claim_and_preserves_ai(tmp_path: Path) -> None:
    app = _browser_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        "/api/accounts/account-01/followback-cooldown",
        json={
            "command_id": "cooldown-01",
            "reason": "platform_follow_reverted",
            "cooldown_seconds": 86_400,
        },
    )
    assert response.status_code == 200
    assert response.json()["followback_circuit_state"] == "cooldown"

    claim = client.post("/api/browser-actions/claim", json={
        **_browser_identity("account-01"),
        "action_type": "followback",
        "action_key": "follow:new",
        "owner_id": "activity-tab",
        "timestamp_ms": 2_000,
        "lease_seconds": 30,
    })
    assert claim.json() == {"claimed": False, "circuit_state": "cooldown"}
    account = _account(client, "account-01")
    assert account["ai_enabled"] is True
```

Add a conflict assertion when enabling follow-back before expiry and a read-model
assertion for circuit state, reason, and expiry.

- [ ] **Step 2: Run focused tests and observe RED**

Run:

```bash
uv run pytest \
  tests/test_dashboard_api.py -k 'followback and circuit' \
  tests/test_lead_api.py -k 'followback and circuit' -q
```

Expected: endpoint and read-model assertions fail.

- [ ] **Step 3: Add request models and API routes**

Add:

```python
class FollowbackCooldownCommand(ApiRequest):
    command_id: CommandId
    reason: Annotated[str, StringConstraints(
        strip_whitespace=True, min_length=1, max_length=100,
    )]
    cooldown_seconds: int = Field(default=86_400, ge=60, le=604_800)
```

Extend `BrowserActionResultRequest` with:

```python
reason: Annotated[str, StringConstraints(max_length=100)] = ""
```

Route `POST /api/accounts/{account_id}/followback-cooldown` through an idempotent
database command. Use `claim_browser_followback_action` and
`finish_browser_followback_action` for follow-back actions. Include
`circuit_state` in rejected claim responses. Keep DM and welcome routes intact.

When `followback-enable` receives `enabled=true`, return `409` with
`followback_cooldown_active` and `cooldown_until_ms` while the circuit is in
cooldown. After expiry, enabling preserves the promoted `canary` state.

- [ ] **Step 4: Expose circuit fields in account read models**

Add these fields wherever browser account controls are returned:

```python
"followback_circuit_state": circuit["state"],
"followback_circuit_reason": circuit["reason"],
"followback_cooldown_until_ms": circuit["cooldown_until_ms"],
```

The operator preference remains `followback_enabled`; effective claims require
both the preference and circuit permission.

- [ ] **Step 5: Run focused API regression**

Run:

```bash
uv run pytest tests/test_dashboard_api.py tests/test_lead_api.py -q
```

Expected: all focused API tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/db.py \
  tests/test_dashboard_api.py tests/test_lead_api.py
git commit -m "feat: gate followback claims with account cooldowns"
```

### Task 3: Chrome Uncertain Reason and Console Visibility

**Files:**
- Modify: `chrome-event-bridge/content.js`
- Modify: `chrome-event-bridge/content.test.js`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/InboxView.tsx`
- Modify: `operator-console/src/InboxView.test.tsx`

- [ ] **Step 1: Write failing Chrome and frontend tests**

Add a content workflow test proving a claimed unresolved follow reports:

```js
assert.equal(actionResult.body.state, "uncertain");
assert.equal(actionResult.body.reason, "followback_unresolved");
```

Add an Inbox test with an account circuit fixture and assert that the account
control displays `回关冷却中` and the switch is disabled while AI remains enabled.

- [ ] **Step 2: Run focused tests and observe RED**

Run:

```bash
node --test --test-name-pattern='followback unresolved reason' \
  chrome-event-bridge/content.test.js
cd operator-console && npm test -- --run src/InboxView.test.tsx
```

Expected: reason and cooldown status assertions fail.

- [ ] **Step 3: Implement the extension result reason**

Track `actionReason` beside `actionState`. Set it to
`followback_unresolved` whenever a claimed action finishes uncertain, and send:

```js
body: {
  ...actionIdentity,
  state: actionState,
  reason: actionReason || "",
}
```

Completed results send an empty reason. Existing processed-record and retry caps
remain unchanged.

- [ ] **Step 4: Implement console circuit rendering**

Extend the account API type with the three circuit fields. Render:

- `回关正常` for `closed`
- `回关冷却中` plus local expiry for `cooldown`
- `等待单次验证` for `canary`

Disable only the follow-back switch during `cooldown`; the AI switch remains
independent.

- [ ] **Step 5: Run Chrome and frontend regression**

Run:

```bash
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
cd operator-console && npm test -- --run && npm run build
```

Expected: all Chrome and frontend tests pass and the production build succeeds.

- [ ] **Step 6: Commit Task 3**

```bash
git add chrome-event-bridge/content.js chrome-event-bridge/content.test.js \
  operator-console/src/api.ts operator-console/src/InboxView.tsx \
  operator-console/src/InboxView.test.tsx
git commit -m "feat: expose followback cooldown state"
```

### Task 4: Seed the Current Cooldown and Document Canary Recovery

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md` only if its implementation-owned checkpoint hunk is cleanly separable

- [ ] **Step 1: Run full applicable verification**

Run:

```bash
uv run pytest -q
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
cd operator-console && npm test -- --run && npm run build
./android-event-bridge/build.sh
uvx ruff check src tests
git diff --check
```

Expected: all task-owned gates pass. If unrelated dirty mobile/acquisition changes
break the full Python suite, record the exact failing tests and also run the
complete browser/API focused suite.

- [ ] **Step 2: Open a 24-hour cooldown for both controlled accounts**

Call the new endpoint with unique command IDs and reason
`platform_follow_reverted`. Confirm browser bindings report AI enabled,
follow-back operator preference disabled, and circuit state `cooldown`.

- [ ] **Step 3: Verify read-only monitoring remains healthy**

Wait at least one watchdog interval and verify Activity plus Messages remain
fresh `4/4`, no follow-back lease is created, and no historical welcome plan is
added.

- [ ] **Step 4: Update the runbook**

Document the circuit state model, current cooldown, the manual mobile account
check path, and the later canary procedure. Do not store message bodies, contact
values, credentials, cookies, tokens, proxy URLs, or personal screenshots.

- [ ] **Step 5: Commit Task 4**

```bash
git add docs/web-engagement-runbook.md
git commit -m "docs: add followback cooldown recovery runbook"
```


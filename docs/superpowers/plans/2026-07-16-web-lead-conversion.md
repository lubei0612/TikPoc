# TikTok Web Lead Conversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chrome DOM-based follow-back and multi-turn AI direct-message flow that turns inbound TikTok interest into private-channel leads without interrupting mobile touch workers.

**Architecture:** Each TikTok account runs in a dedicated Chrome Profile with Activity and Messages tabs. The extension turns semantic DOM changes into idempotent localhost requests; Python persists conversations and reply plans, generates one AI reply per inbound fingerprint, and records funnel state. All visible clicks and sends use account-scoped leases plus post-action verification.

**Tech Stack:** Python 3.12, SQLite, `http.server`, PyYAML, pytest, OpenAI-compatible chat completions, Chrome Manifest V3, JavaScript `MutationObserver`, Web Crypto SHA-256, Node test runner.

---

## File Map

- Modify `src/tikpoc/web_accounts.py`: support browser-only accounts and conversion settings.
- Create `src/tikpoc/lead_conversion.py`: pure conversation stages, contact detection, invite policy, and prompt context.
- Create `src/tikpoc/browser_dm.py`: idempotent reply planning and result reconciliation service.
- Modify `src/tikpoc/messaging.py`: accept offer, FAQ, stage, and invitation context.
- Modify `src/tikpoc/db.py`: reply plans, action leases, browser health, funnel events, and coverage queries.
- Modify `src/tikpoc/dashboard.py`: browser DM, lease, health, lead, and sale endpoints.
- Create `chrome-event-bridge/dm-core.js`: pure message normalization, identity, and reconciliation helpers.
- Create `chrome-event-bridge/dm-content.js`: Messages DOM observer and visible composer executor.
- Modify `chrome-event-bridge/background.js`: localhost transport for plans, results, leases, and health.
- Modify `chrome-event-bridge/content.js`: claim follow-back actions before clicking.
- Modify `chrome-event-bridge/manifest.json`: load the DM scripts and enable extension alarms/tabs.
- Modify `chrome-event-bridge/options.html`: expose DM and page-role settings.
- Modify `chrome-event-bridge/options.js`: persist the new settings.
- Modify `src/tikpoc/static/dashboard.html`: add coverage, browser health, and lead funnel sections.
- Modify `src/tikpoc/static/dashboard.js`: render the new operational data and record sales.
- Modify `src/tikpoc/static/dashboard.css`: style the added dense operational sections.
- Modify `config/web-accounts.example.yaml`: document browser account conversion settings.
- Modify `docs/web-engagement-runbook.md`: document seven-profile setup and live acceptance.
- Create `tests/test_lead_conversion.py`: pure conversion policy coverage.
- Create `tests/test_browser_dm.py`: reply-plan service coverage.
- Modify `tests/test_web_accounts.py`: browser-mode parsing coverage.
- Modify `tests/test_web_events_db.py`: persistence, lease, funnel, and coverage coverage.
- Modify `tests/test_dashboard_api.py`: browser DM and lead HTTP coverage.
- Modify `tests/test_messaging.py`: prompt-context coverage.
- Modify `tests/test_dashboard_static.py`: new dashboard surface coverage.
- Create `chrome-event-bridge/dm-core.test.js`: DM core Node tests.

### Task 1: Browser-Only Account Configuration

**Files:**
- Modify: `src/tikpoc/web_accounts.py:7-75`
- Modify: `tests/test_web_accounts.py`
- Modify: `config/web-accounts.example.yaml`

- [ ] **Step 1: Write failing browser-mode registry tests**

```python
def test_browser_account_does_not_require_business_credentials(tmp_path: Path) -> None:
    path = tmp_path / "accounts.yaml"
    path.write_text(
        """
accounts:
  - account_id: account-01
    device_id: phone-01
    mode: browser
    private_channel_hint: "WhatsApp: +1 555 0100"
    offer_context: "Bags from the current catalog"
    faq_file: faq.md
    browser_followback_enabled: true
    browser_dm_enabled: true
""",
        encoding="utf-8",
    )
    (tmp_path / "faq.md").write_text("Shipping takes 5-7 days.", encoding="utf-8")

    account = WebAccountRegistry.from_path(path).by_account_id("account-01")

    assert account.mode == "browser"
    assert account.business_id == ""
    assert account.token_file is None
    assert account.offer_context == "Bags from the current catalog"
    assert account.faq_text == "Shipping takes 5-7 days."
    assert account.browser_dm_enabled is True


def test_business_mode_still_requires_business_fields(tmp_path: Path) -> None:
    path = tmp_path / "accounts.yaml"
    path.write_text(
        "accounts:\n  - account_id: a\n    device_id: d\n    mode: business\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="business_id and token_file"):
        WebAccountRegistry.from_path(path)
```

- [ ] **Step 2: Run the focused tests and verify the new fields are missing**

Run: `uv run pytest tests/test_web_accounts.py -q`

Expected: FAIL because `WebAccount` has no `mode`, `offer_context`, `faq_text`, or browser enable fields.

- [ ] **Step 3: Extend `WebAccount` and mode-aware parsing**

```python
@dataclass(frozen=True)
class WebAccount:
    account_id: str
    device_id: str
    business_id: str = ""
    token_file: Path | None = None
    mode: str = "browser"
    private_channel_hint: str = ""
    offer_context: str = ""
    faq_text: str = ""
    reply_language: str = "auto"
    max_auto_replies: int = 12
    invite_after_meaningful_turns: int = 2
    fallback_acknowledgement: str = "Thanks for your message. What are you looking for?"
    browser_followback_enabled: bool = True
    browser_dm_enabled: bool = True
    enabled: bool = True
```

Keep the first four fields in their existing positional order. Parse `mode` as
`browser` or `business`; when an old entry omits it, infer `business` if either
Business field is present and otherwise infer `browser`. Resolve `faq_file`
relative to the YAML file, require `business_id` plus `token_file` for Business
mode, and build `_by_business_id` from nonempty IDs only.

- [ ] **Step 4: Run registry and web-worker tests**

Run: `uv run pytest tests/test_web_accounts.py tests/test_web_worker.py -q`

Expected: PASS. Existing Business account fixtures continue to parse.

- [ ] **Step 5: Update the example account file**

Use one enabled browser entry containing every field from the test and a second disabled browser entry for `account-02`. Keep secrets and real contact values out of the tracked example.

- [ ] **Step 6: Commit the account-mode change**

```bash
git add src/tikpoc/web_accounts.py tests/test_web_accounts.py config/web-accounts.example.yaml
git commit -m "feat: add browser web account mode"
```

### Task 2: Durable Browser Reply Plans And Action Leases

**Files:**
- Modify: `src/tikpoc/db.py:191-256,650-770`
- Modify: `tests/test_web_events_db.py`

- [ ] **Step 1: Write failing reply-plan and lease tests**

```python
def test_reply_plan_is_unique_per_account_and_inbound_fingerprint(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()

    first, created = db.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1000
    )
    second, duplicate_created = db.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1000
    )

    assert created is True
    assert duplicate_created is False
    assert second.id == first.id


def test_expired_browser_action_lease_can_be_reclaimed(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()

    assert db.claim_browser_action("account-01", "dm_send", "plan-1", "tab-a", 1000, 30)
    assert not db.claim_browser_action("account-01", "dm_send", "plan-1", "tab-b", 2000, 30)
    assert db.claim_browser_action("account-01", "dm_send", "plan-1", "tab-b", 32000, 30)
```

- [ ] **Step 2: Run the database tests and verify the methods are absent**

Run: `uv run pytest tests/test_web_events_db.py -q`

Expected: FAIL with missing `reserve_browser_reply_plan` and `claim_browser_action` methods.

- [ ] **Step 3: Add immutable reply-plan data**

```python
@dataclass(frozen=True)
class BrowserReplyPlan:
    id: int
    account_id: str
    conversation_id: str
    inbound_fingerprint: str
    participant_username: str
    inbound_text: str
    inbound_timestamp_ms: int
    reply_text: str
    stage: str
    state: str
```

- [ ] **Step 4: Add additive SQLite migrations**

Create `browser_reply_plans` with a unique `(account_id, inbound_fingerprint)`, exact draft text, stage, and `planning|planned|sent|uncertain|superseded` state. Create `browser_action_leases` with primary key `(account_id, action_type, action_key)`, owner, lease expiry in milliseconds, and state. Add these columns to `web_conversations` when absent: `stage`, `meaningful_turns`, `auto_reply_count`, `last_invited_at_ms`, `contact_captured_at_ms`, and `human_required`.

- [ ] **Step 5: Implement transactional plan and lease methods**

```python
def reserve_browser_reply_plan(
    self,
    account_id: str,
    conversation_id: str,
    inbound_fingerprint: str,
    participant_username: str,
    inbound_text: str,
    inbound_timestamp_ms: int,
) -> tuple[BrowserReplyPlan, bool]: ...

def complete_browser_reply_plan(
    self, plan_id: int, *, reply_text: str, stage: str
) -> BrowserReplyPlan: ...

def set_browser_reply_plan_state(self, plan_id: int, state: str) -> None: ...

def claim_browser_action(
    self,
    account_id: str,
    action_type: str,
    action_key: str,
    owner_id: str,
    now_ms: int,
    lease_seconds: int = 30,
) -> bool: ...

def finish_browser_action(
    self, account_id: str, action_type: str, action_key: str, owner_id: str, state: str
) -> bool: ...
```

Use `BEGIN IMMEDIATE` for every reservation or claim. Permit a claim when the row is absent or its noncompleted lease has expired. Accept only `completed`, `uncertain`, and `superseded` results.

- [ ] **Step 6: Run database tests**

Run: `uv run pytest tests/test_web_events_db.py tests/test_db.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the persistence primitives**

```bash
git add src/tikpoc/db.py tests/test_web_events_db.py
git commit -m "feat: persist browser reply plans and action leases"
```

### Task 3: Pure Lead Conversation Policy

**Files:**
- Create: `src/tikpoc/lead_conversion.py`
- Create: `tests/test_lead_conversion.py`

- [ ] **Step 1: Write failing state-transition tests**

```python
def test_second_meaningful_turn_invites_to_private_channel() -> None:
    result = assess_inbound(
        previous_stage=ConversationStage.ENGAGED,
        text="Can you show me the available black bags?",
        meaningful_turns=1,
        invite_after_meaningful_turns=2,
        last_invited_at_ms=0,
        now_ms=100_000,
    )
    assert result.stage == ConversationStage.QUALIFIED
    assert result.should_invite is True


def test_contact_capture_has_priority_over_invitation() -> None:
    result = assess_inbound(
        previous_stage=ConversationStage.INVITED,
        text="My WhatsApp is +44 7700 900123",
        meaningful_turns=3,
        invite_after_meaningful_turns=2,
        last_invited_at_ms=1,
        now_ms=100_000,
    )
    assert result.stage == ConversationStage.CONTACT_CAPTURED
    assert result.contact == "+44 7700 900123"
    assert result.should_invite is False


@pytest.mark.parametrize("text", ["I want a refund", "payment failed", "我要投诉"])
def test_payment_or_complaint_requires_human(text: str) -> None:
    result = assess_inbound(
        previous_stage=ConversationStage.ENGAGED,
        text=text,
        meaningful_turns=1,
        invite_after_meaningful_turns=2,
        last_invited_at_ms=0,
        now_ms=100_000,
    )
    assert result.stage == ConversationStage.HUMAN_REQUIRED
```

- [ ] **Step 2: Run the new test file and verify import failure**

Run: `uv run pytest tests/test_lead_conversion.py -q`

Expected: FAIL because `tikpoc.lead_conversion` does not exist.

- [ ] **Step 3: Implement the pure policy**

```python
class ConversationStage(StrEnum):
    NEW = "new"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    INVITED = "invited"
    CONTACT_CAPTURED = "contact_captured"
    HUMAN_REQUIRED = "human_required"
    CLOSED = "closed"


@dataclass(frozen=True)
class ConversionAssessment:
    stage: ConversationStage
    meaningful: bool
    should_invite: bool
    contact: str = ""
    human_reason: str = ""
```

Implement `extract_contact(text)`, `is_meaningful(text)`, `requires_human(text)`, `shows_buying_intent(text)`, `assess_inbound(...)`, and `build_lead_prompt(...)`. Normalize Unicode and whitespace, support English and Chinese interest/handoff terms, apply a 24-hour invitation cooldown, and cap all prompt context before concatenation.

- [ ] **Step 4: Run policy tests and lint**

Run: `uv run pytest tests/test_lead_conversion.py -q && uv run ruff check src/tikpoc/lead_conversion.py tests/test_lead_conversion.py`

Expected: PASS and Ruff clean.

- [ ] **Step 5: Commit the policy**

```bash
git add src/tikpoc/lead_conversion.py tests/test_lead_conversion.py
git commit -m "feat: add lead conversation policy"
```

### Task 4: Context-Aware AI Replies

**Files:**
- Modify: `src/tikpoc/messaging.py:50-109`
- Modify: `tests/test_messaging.py`

- [x] **Step 1: Write a failing prompt-context test**

```python
def test_lead_reply_prompt_contains_offer_stage_and_conditional_invite() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse("Happy to help. WhatsApp: +1 555 0100")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )
    client.reply_conversation(
        [{"direction": "inbound", "text": "Do you have black bags?"}],
        private_channel_hint="WhatsApp: +1 555 0100",
        offer_context="Black bags are available in the current catalog.",
        faq_context="Shipping usually takes 5-7 days.",
        conversation_stage="qualified",
        should_invite=True,
    )

    system = json.loads(requests[0].data)["messages"][0]["content"]
    assert "Conversation stage: qualified" in system
    assert "Black bags are available" in system
    assert "WhatsApp: +1 555 0100" in system
```

- [x] **Step 2: Run the focused test and verify unexpected keyword failure**

Run: `uv run pytest tests/test_messaging.py -q`

Expected: FAIL because `reply_conversation` lacks the new keyword arguments.

- [x] **Step 3: Extend `reply_conversation`**

Add keyword-only `offer_context`, `faq_context`, `conversation_stage`, `should_invite`, and `fallback` parameters. Include the private channel only when `should_invite` is true. Use the per-account fallback for missing configuration or provider errors. Retain the same-language, concise, fact-bound response rules.

- [x] **Step 4: Run messaging tests**

Run: `uv run pytest tests/test_messaging.py -q`

Expected: PASS.

- [x] **Step 5: Commit the AI prompt change**

```bash
git add src/tikpoc/messaging.py tests/test_messaging.py
git commit -m "feat: add conversion context to AI replies"
```

### Task 5: Browser DM Planning Service

**Files:**
- Create: `src/tikpoc/browser_dm.py`
- Create: `tests/test_browser_dm.py`
- Modify: `src/tikpoc/db.py`

- [ ] **Step 1: Write failing idempotency and result tests**

```python
class FakeReplyClient:
    def __init__(self) -> None:
        self.calls = 0

    def reply_conversation(self, history, **kwargs):
        self.calls += 1
        return "Yes. You can continue on WhatsApp: +1 555 0100"


def test_same_inbound_fingerprint_generates_one_ai_draft(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    ai = FakeReplyClient()
    service = BrowserDmService(db, registry_with_browser_account(), ai, clock=lambda: 100.0)
    inbound = BrowserInbound("account-01", "phone-01", "conversation-01", "fp-01", "buyer", "Do you ship?", 99_000)

    first = service.plan(inbound)
    second = service.plan(inbound)

    assert first.plan_id == second.plan_id
    assert first.reply_text == second.reply_text
    assert ai.calls == 1


def test_confirmed_result_appends_outbound_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    service = BrowserDmService(db, registry_with_browser_account(), FakeReplyClient(), clock=lambda: 100.0)
    plan = service.plan(BrowserInbound("account-01", "phone-01", "c", "fp", "buyer", "hello", 99_000))

    service.record_result("account-01", "phone-01", plan.plan_id, "sent")
    service.record_result("account-01", "phone-01", plan.plan_id, "sent")

    messages = db.recent_web_messages("account-01", "c", limit=20)
    assert [row["direction"] for row in messages] == ["inbound", "outbound"]


def test_reply_budget_closes_after_twelve_confirmed_replies(tmp_path: Path) -> None:
    service, db = service_with_confirmed_history(tmp_path, outbound_count=12)
    plan = service.plan(BrowserInbound("account-01", "phone-01", "c", "fp-13", "buyer", "one more question", 99_000))
    assert plan.stage == "closed"
    assert plan.reply_text == ""
```

Define `registry_with_browser_account()` in the same test file with one enabled
browser `WebAccount`, the configured WhatsApp hint, offer, FAQ, and a 12-reply
budget. Define `service_with_confirmed_history()` by appending alternating
inbound/outbound rows through `Database.append_web_message` before constructing
the service.

- [ ] **Step 2: Run the tests and verify module import failure**

Run: `uv run pytest tests/test_browser_dm.py -q`

Expected: FAIL because `tikpoc.browser_dm` does not exist.

- [ ] **Step 3: Implement immutable request and response records**

```python
@dataclass(frozen=True)
class BrowserInbound:
    account_id: str
    device_id: str
    conversation_id: str
    fingerprint: str
    participant_username: str
    text: str
    timestamp_ms: int


@dataclass(frozen=True)
class BrowserReply:
    plan_id: int
    conversation_id: str
    inbound_fingerprint: str
    reply_text: str
    stage: str
```

- [ ] **Step 4: Implement `BrowserDmService`**

Use one `threading.Lock` per `account_id`. Validate the registry mapping, return
an existing completed plan before calling AI, append the inbound message with its
fingerprint as the message ID, load conversation state/history, enforce
`account.max_auto_replies`, call `assess_inbound`, generate the exact reply,
complete the plan, and update stage counters atomically. `record_result` accepts
`sent`, `uncertain`, or `superseded`; only `sent` appends the outbound message and
advances reply/funnel counters. Record `invite_configuration_missing` when the
policy requests an invitation and the account has no destination.

- [ ] **Step 5: Run service, database, and messaging tests**

Run: `uv run pytest tests/test_browser_dm.py tests/test_web_events_db.py tests/test_messaging.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the service**

```bash
git add src/tikpoc/browser_dm.py src/tikpoc/db.py tests/test_browser_dm.py
git commit -m "feat: plan idempotent browser DM replies"
```

### Task 6: Browser DM, Lease, And Health HTTP Endpoints

**Files:**
- Modify: `src/tikpoc/dashboard.py:19-285`
- Modify: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing end-to-end HTTP tests**

Add a fake `BrowserDmService` and cover these exact routes:

```python
def test_browser_dm_plan_and_result_endpoints(tmp_path: Path) -> None:
    service = FakeBrowserDmService()
    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=browser_registry(tmp_path),
        browser_dm_service=service,
    )
    try:
        planned = post_json(base_url + "/api/browser-dm/reply-plan", browser_inbound_body())
        recorded = post_json(
            base_url + "/api/browser-dm/reply-result",
            {"account_id": "account-01", "device_id": "phone-01", "plan_id": planned["plan_id"], "state": "sent"},
        )
        assert planned["reply_text"] == "Thanks. WhatsApp: +1 555 0100"
        assert recorded == {"recorded": True}
        assert service.results == [(planned["plan_id"], "sent")]
    finally:
        server.shutdown()
```

Also test `/api/browser-actions/claim`, `/api/browser-actions/result`, and `/api/browser-health`, including a mismatched account/device returning HTTP 400.

Implement `post_json` in the test with `urllib.request.Request`, reuse the
existing `_start_server` thread helper, and define `FakeBrowserDmService.plan`
and `record_result` with the exact `BrowserReply` fields from Task 5.

- [ ] **Step 2: Run the endpoint tests and verify 404 responses**

Run: `uv run pytest tests/test_dashboard_api.py -q`

Expected: FAIL because the new routes return 404.

- [ ] **Step 3: Inject the browser DM service**

Add `browser_dm_service: BrowserDmService | None` to `DashboardServer` and `create_server`. When a registry exists and no service is injected, construct `BrowserDmService(database, registry, AiReplyClient.from_environment())`.

- [ ] **Step 4: Add strict JSON handlers**

Implement private handlers for reply plan/result, lease claim/result, and health. Reuse `_required_text`, validate account/device mapping once, enforce integer plan IDs and timestamps, and return JSON objects with stable keys. Extend `OPTIONS` handling to every browser POST path.

- [ ] **Step 5: Run endpoint and full Python tests**

Run: `uv run pytest tests/test_dashboard_api.py -q && uv run pytest -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit the endpoints**

```bash
git add src/tikpoc/dashboard.py tests/test_dashboard_api.py
git commit -m "feat: expose browser DM conversion endpoints"
```

### Task 7: Pure JavaScript DM Core

**Files:**
- Create: `chrome-event-bridge/dm-core.js`
- Create: `chrome-event-bridge/dm-core.test.js`

- [ ] **Step 1: Write failing Node tests**

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const dm = require("./dm-core.js");

test("builds a stable SHA-256 fingerprint", async () => {
  const input = {
    accountId: "account-01",
    conversationId: "messages:buyer",
    sender: "buyer",
    messageId: "visible-10",
    timestamp: "10:30",
    text: "  Hello   there ",
  };
  assert.equal(await dm.fingerprintMessage(input), await dm.fingerprintMessage({...input, text: "Hello there"}));
});

test("accepts only latest inbound descriptors", () => {
  assert.equal(dm.isActionableInbound({direction: "inbound", text: "hello"}), true);
  assert.equal(dm.isActionableInbound({direction: "outbound", text: "hello"}), false);
  assert.equal(dm.isActionableInbound({direction: "unknown", text: "hello"}), false);
});

test("reconciles an exact outbound bubble", () => {
  assert.equal(dm.hasMatchingOutbound("Thanks for asking", [
    {direction: "inbound", text: "Hi"},
    {direction: "outbound", text: " Thanks for asking "},
  ]), true);
});
```

- [ ] **Step 2: Run Node tests and verify module-not-found failure**

Run: `node --test chrome-event-bridge/dm-core.test.js`

Expected: FAIL because `dm-core.js` does not exist.

- [ ] **Step 3: Implement the UMD-style core module**

Export `normalizeText`, `conversationKey`, `isActionableInbound`, `fingerprintMessage`, `sameInbound`, `findSemanticButton`, and `hasMatchingOutbound`. Use `globalThis.crypto.subtle.digest("SHA-256", ...)` in Chrome and `require("node:crypto").webcrypto` in Node. Accept only `tiktok.com` Messages URLs or a normalized username fallback.

- [ ] **Step 4: Run all extension core tests**

Run: `node --test chrome-event-bridge/core.test.js chrome-event-bridge/dm-core.test.js`

Expected: PASS.

- [ ] **Step 5: Commit the core**

```bash
git add chrome-event-bridge/dm-core.js chrome-event-bridge/dm-core.test.js
git commit -m "feat: add browser DM identity core"
```

### Task 8: Messages DOM Observer And Visible Send Executor

**Files:**
- Create: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/background.js:1-62`
- Modify: `chrome-event-bridge/manifest.json`

- [ ] **Step 1: Add localhost transport messages**

Define these message types in `background.js`: `TIKPOC_DM_PLAN`, `TIKPOC_DM_RESULT`, `TIKPOC_ACTION_CLAIM`, `TIKPOC_ACTION_RESULT`, and `TIKPOC_BROWSER_HEALTH`. Route each through a shared `postLocal(dashboardUrl, path, body)` that retains the existing localhost origin validation.

- [ ] **Step 2: Add DM scripts and permissions to the manifest**

Load scripts in this order: `core.js`, `dm-core.js`, `content.js`, `dm-content.js`. Add `alarms` and `tabs` permissions while retaining `storage` and localhost host permissions.

- [ ] **Step 3: Implement the page adapter in `dm-content.js`**

Create focused functions `pageRole`, `visible`, `elementLabel`, `conversationRows`, `openConversation`, `readActiveConversation`, `findComposer`, `setComposerText`, `findSendButton`, and `waitForOutbound`. Prefer accessible roles, labels, links, and `data-e2e` attributes; treat hashed class names as diagnostics only.

- [ ] **Step 4: Implement the serialized observer**

On `/messages`, establish a per-account baseline in `chrome.storage.local`, observe `document.documentElement`, debounce for 250 ms, and process one changed/unread row at a time. Build the inbound fingerprint, request a plan, re-read the active thread, claim `dm_send:<plan_id>`, fill the composer, click Send, reconcile the outbound bubble, post the result, and release the lease.

- [ ] **Step 5: Add browser health reporting**

Report account, device, `messages` page role, URL path, signed-in signal, and current timestamp on load and every extension alarm. Exclude message text from health payloads.

- [ ] **Step 6: Run JS syntax and core tests**

Run: `node --check chrome-event-bridge/background.js && node --check chrome-event-bridge/dm-content.js && node --test chrome-event-bridge/core.test.js chrome-event-bridge/dm-core.test.js`

Expected: syntax checks and tests PASS.

- [ ] **Step 7: Commit the DM content bridge**

```bash
git add chrome-event-bridge/background.js chrome-event-bridge/dm-content.js chrome-event-bridge/manifest.json
git commit -m "feat: send AI replies through TikTok Web"
```

### Task 9: Lease-Protected Follow-Back And Extension Options

**Files:**
- Modify: `chrome-event-bridge/content.js:117-193`
- Modify: `chrome-event-bridge/options.html`
- Modify: `chrome-event-bridge/options.js`
- Modify: `chrome-event-bridge/popup.js`
- Modify: `chrome-event-bridge/core.js`
- Modify: `chrome-event-bridge/core.test.js`

- [ ] **Step 1: Gate follow-back clicks on a server lease**

Before `candidate.button.click()`, request `TIKPOC_ACTION_CLAIM` with action type `followback`, follower dedup key, and a per-tab owner UUID. If the claim is busy, skip the click. After state verification, post `completed` or `uncertain` through `TIKPOC_ACTION_RESULT`.

- [ ] **Step 2: Prefer an Activity event identity in follower deduplication**

Extend `buildFollowerDedupKey(accountId, username, eventId = "")`. Normalize and
append a DOM-provided event ID when present; retain account plus username when it
is absent. Add Node assertions for both forms and extract event identity from
stable `data-*` attributes before visible timestamps.

- [ ] **Step 3: Add independent enable switches**

Persist `browserFollowbackEnabled` and `browserDmEnabled`. Keep `enabled` as the master switch. The Activity observer reads the former; the Messages observer reads the latter.

- [ ] **Step 4: Expose readiness in the popup**

Show account ID, device ID, follow-back enabled, DM enabled, and the last local connection test. Use text-only status and the existing compact popup layout.

- [ ] **Step 5: Run extension tests and syntax checks**

Run: `node --check chrome-event-bridge/content.js chrome-event-bridge/options.js chrome-event-bridge/popup.js && node --test chrome-event-bridge/core.test.js chrome-event-bridge/dm-core.test.js`

Expected: PASS.

- [ ] **Step 6: Commit the lease and settings change**

```bash
git add chrome-event-bridge/core.js chrome-event-bridge/core.test.js chrome-event-bridge/content.js chrome-event-bridge/options.html chrome-event-bridge/options.js chrome-event-bridge/popup.js
git commit -m "feat: coordinate browser followback and DM actions"
```

### Task 10: Funnel Persistence, Coverage, And Sales

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/browser_dm.py`
- Modify: `tests/test_web_events_db.py`
- Modify: `tests/test_browser_dm.py`

- [ ] **Step 1: Write failing funnel and coverage tests**

```python
def test_batch_coverage_counts_only_confirmed_profile_visits(tmp_path: Path) -> None:
    db = seeded_seven_device_database(tmp_path)
    assert db.batch_coverage("batch-01", expected_devices=7) == {
        "targets": 2,
        "fully_covered": 1,
        "coverage_rate": 0.5,
    }


def test_funnel_events_are_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.migrate()
    assert db.record_lead_funnel_event("account-01", "buyer", "contact_captured", "fp-1")
    assert not db.record_lead_funnel_event("account-01", "buyer", "contact_captured", "fp-1")
    assert db.lead_funnel_snapshot()["contact_captured"] == 1
```

Build `seeded_seven_device_database()` in the test by inserting two targets for
all seven device IDs, marking every first-target task `completed` with a
`profile_opened` checkpoint, and leaving one second-target device pending.

- [ ] **Step 2: Run focused tests and verify missing methods**

Run: `uv run pytest tests/test_web_events_db.py tests/test_browser_dm.py -q`

Expected: FAIL with missing coverage and funnel methods.

- [ ] **Step 3: Add funnel and sale tables**

Create `lead_funnel_events` with a unique `(account_id, participant_username, stage, source_key)` and `lead_sales` with account, participant, amount in integer minor units, currency, status, and timestamp. Create `browser_account_health` keyed by account and page role.

- [ ] **Step 4: Implement aggregation methods**

Add `record_lead_funnel_event`, `lead_funnel_snapshot`, `recent_leads`,
`record_lead_sale`, `browser_health_snapshot`, `upsert_browser_health`,
`reply_latency_snapshot`, and `batch_coverage`. Compute confirmed-reply latency
from reply-plan creation to `sent` update and expose median and p90 values. A
successful visit is a `completed` or `skipped` task whose checkpoint begins with
`profile_opened` or `post_opened:`.

- [ ] **Step 5: Emit funnel events from `BrowserDmService`**

Record `dm_inbound`, `engaged`, `qualified`, `invited`, `contact_captured`, and `human_required` using the inbound fingerprint as the source key. The database unique constraint handles retries.

- [ ] **Step 6: Run persistence and service tests**

Run: `uv run pytest tests/test_web_events_db.py tests/test_browser_dm.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the funnel data layer**

```bash
git add src/tikpoc/db.py src/tikpoc/browser_dm.py tests/test_web_events_db.py tests/test_browser_dm.py
git commit -m "feat: track lead funnel and fleet coverage"
```

### Task 11: Operational Dashboard For Leads And Browser Health

**Files:**
- Modify: `src/tikpoc/dashboard.py`
- Modify: `src/tikpoc/static/dashboard.html`
- Modify: `src/tikpoc/static/dashboard.js`
- Modify: `src/tikpoc/static/dashboard.css`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_dashboard_static.py`

- [ ] **Step 1: Write failing API and static-surface tests**

Test `GET /api/leads?limit=20`, funnel and health fields in `GET /api/status`, and `POST /api/leads/sale`. Extend the static test to require `coverageRate`, `browserAccounts`, `leadFunnel`, `recentLeads`, and `recordSaleButton` IDs.

- [ ] **Step 2: Run dashboard tests and verify failures**

Run: `uv run pytest tests/test_dashboard_api.py tests/test_dashboard_static.py -q`

Expected: FAIL because the route and element IDs do not exist.

- [ ] **Step 3: Add the lead APIs**

Include `coverage`, `lead_funnel`, and `browser_health` in `/api/status`; add bounded recent-lead retrieval; validate sale amount as a positive integer minor-unit value and currency as three uppercase ASCII letters.

- [ ] **Step 4: Build the compact operations UI**

Add a coverage band, browser account table, seven-stage funnel, recent lead table, and an inline sale-record dialog. Keep existing task controls, use semantic tables for scanability, and keep cards at or below 8 px radius.

- [ ] **Step 5: Render and verify responsive layout**

Run the dashboard on an unused localhost port. Use Playwright screenshots at 1440x900 and 390x844, verify no overlap or clipped text, and confirm the controls still work.

- [ ] **Step 6: Run dashboard and full test suites**

Run: `uv run pytest tests/test_dashboard_api.py tests/test_dashboard_static.py -q && uv run pytest -q && uv run ruff check .`

Expected: PASS and Ruff clean.

- [ ] **Step 7: Commit the operations dashboard**

```bash
git add src/tikpoc/dashboard.py src/tikpoc/static tests/test_dashboard_api.py tests/test_dashboard_static.py
git commit -m "feat: show browser leads and conversion funnel"
```

### Task 12: Runbook, Regression, And Live Chrome Calibration

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `docs/chrome-account-binding-handoff.md`
- Modify: `launchd/com.tikpoc.dashboard.8766.plist`

- [ ] **Step 1: Update production configuration**

Document seven Chrome Profiles, one account mapping per profile, Activity and Messages pinned tabs, real private-channel configuration, and browser-only registry mode. Remove Business API setup from the default path and retain it under an optional compatibility section.

- [ ] **Step 2: Run automated verification**

Run:

```bash
uv run pytest -q
uv run ruff check .
node --test chrome-event-bridge/core.test.js chrome-event-bridge/dm-core.test.js
node --check chrome-event-bridge/core.js chrome-event-bridge/content.js chrome-event-bridge/dm-core.js chrome-event-bridge/dm-content.js chrome-event-bridge/background.js chrome-event-bridge/options.js chrome-event-bridge/popup.js
plutil -lint launchd/com.tikpoc.dashboard.8766.plist
```

Expected: every command exits 0.

- [ ] **Step 3: Run a localhost synthetic end-to-end check**

Start the LaunchAgent-backed dashboard, post one inbound body twice, verify both responses contain the same plan ID and exact draft, post `sent`, and verify one inbound plus one outbound message in SQLite.

- [ ] **Step 4: Calibrate the real TikTok Messages DOM**

With the ChatGPT Chrome control connection active, inspect only visible/interactive state in the signed-in TikTok tab. Record stable accessible labels and `data-e2e` attributes for conversation rows, unread state, inbound/outbound bubbles, composer, Send, Activity follower rows, and follow controls. Update semantic adapters and rerun JS tests after every selector adjustment.

- [ ] **Step 5: Perform account-01 live acceptance**

From a second controlled account: follow, send three inbound messages, accept the private-channel invitation, reload Messages, and rerender the thread. Confirm one follow-back, one reply per inbound, `contact_captured`, no duplicate sends, and uninterrupted mobile task advancement.

- [ ] **Step 6: Roll out profiles 02 through 07**

Repeat the connection test and live acceptance for each mapping. Mark an account ready only when both Activity and Messages health rows are current and all seven acceptance checks in the design spec pass.

- [ ] **Step 7: Commit the runbook and calibrated selectors**

```bash
git add docs/web-engagement-runbook.md docs/chrome-account-binding-handoff.md launchd/com.tikpoc.dashboard.8766.plist chrome-event-bridge
git commit -m "docs: finalize browser lead conversion rollout"
```

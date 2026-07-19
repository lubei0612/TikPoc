# Autonomous Asynchronous Customer Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep each bound TikTok Chrome Profile observing new followers and messages, send one product-interest welcome after a verified follow-back, let AI handle asynchronous conversations, route interested customers to profile contact information, and stop after explicit opt-out.

**Architecture:** Extend the existing conversion assessment instead of adding a parallel conversation engine. Browser plans and account-scoped leases remain the durable action boundary; content scripts gain bounded watchdog triggers and both TikTok Messages route families. Existing `closed` conversations represent durable stop-contact, while former human-handoff reasons become replyable profile-contact cases.

**Tech Stack:** Python 3.12, SQLite, FastAPI/Pydantic, Chrome Manifest V3, plain JavaScript, Node test runner, pytest.

---

### Task 1: Autonomous Policy, Product Welcome, And Profile Contact

**Files:**
- Modify: `src/tikpoc/lead_conversion.py`
- Modify: `src/tikpoc/browser_dm.py`
- Modify: `src/tikpoc/messaging.py`
- Modify: `tests/test_lead_conversion.py`
- Modify: `tests/test_browser_dm.py`
- Modify: `tests/test_messaging.py`
- Modify: `tests/test_browser_welcome.py`

- [ ] **Step 1: Write failing conversion-policy tests**

Add parameterized cases proving explicit stop-contact in English, Chinese, German, Spanish, and French returns `ConversationStage.CLOSED` with `stop_contact_reason="explicit_opt_out"`. Add cases proving payment, refund, complaint, cancellation, unsupported-discount, and representative requests return a replyable `QUALIFIED` assessment with `profile_contact_reason` instead of `HUMAN_REQUIRED`. Keep a negative case such as `I am not interested in red; do you have black?` replyable.

```python
@pytest.mark.parametrize(
    "text",
    [
        "stop messaging me",
        "不要再联系我",
        "hör auf mir zu folgen",
        "no me contactes más",
        "ne me contactez plus",
    ],
)
def test_explicit_stop_contact_closes_without_reply(text: str) -> None:
    result = assess_inbound(ConversationStage.ENGAGED, text, 1, 2, 0, 10_000)
    assert result.stage == ConversationStage.CLOSED
    assert result.stop_contact_reason == "explicit_opt_out"
    assert result.should_invite is False
```

- [ ] **Step 2: Run focused policy tests and observe the expected failure**

Run: `uv run pytest tests/test_lead_conversion.py -q`

Expected: new stop-contact fields/cases fail and former handoff cases still produce `human_required`.

- [ ] **Step 3: Implement assessment fields and bounded multilingual stop patterns**

Extend `ConversionAssessment` with defaulted `profile_contact_reason` and `stop_contact_reason`. Add explicit phrase patterns that require a contact/follow/message stop instruction, check them before terminal/handoff handling, and map `_human_reason(text)` to `QUALIFIED`, `should_invite=True`, and `profile_contact_reason=<reason>`. Preserve existing `CLOSED` behavior and monotonic terminal stages.

```python
@dataclass(frozen=True)
class ConversionAssessment:
    stage: ConversationStage
    meaningful: bool
    should_invite: bool
    contact: str = ""
    human_reason: str = ""
    profile_contact_reason: str = ""
    stop_contact_reason: str = ""
```

- [ ] **Step 4: Write failing prompt and browser-plan tests**

Add tests proving:

- a new-follower welcome prompt explicitly asks about the configured product facts and does not include stored WhatsApp/Telegram values;
- an interested or formerly escalated inbound uses a profile-link/pinned-post instruction without exposing `private_channel_hint`, WhatsApp, or Telegram;
- the browser DM service calls the AI for a refund/representative request and does not set `human_required`;
- explicit stop-contact creates an empty `closed` plan and does not call the AI client;
- profile-contact invitation evidence becomes durable only for a nonempty planned reply.

```python
assert "mirror-quality bags" in system
assert "link on the TikTok account profile" in system
assert "pinned profile posts" in system
assert "SECRET_DESTINATION" not in system
```

- [ ] **Step 5: Run focused messaging and DM tests and observe the expected failure**

Run: `uv run pytest tests/test_messaging.py tests/test_browser_dm.py tests/test_browser_welcome.py -q`

Expected: prompt assertions and autonomous former-handoff behavior fail.

- [ ] **Step 6: Implement the product-interest and profile-contact prompt contract**

Add `profile_contact_due` and `profile_contact_reason` arguments to `reply_conversation` and `_build_system_prompt`. For `new_follower_welcome`, require one direct product-interest question grounded in `offer_context`. For a due contact route, instruct the model to mention only the TikTok profile link or pinned-post contact details and never a stored direct destination or live-transfer promise.

Update `BrowserDmService.plan` to pass no direct destination, set `profile_contact_due` from buying intent, meaningful-turn threshold, or a former handoff reason, and keep the 24-hour cooldown. A stop-contact assessment finalizes an empty `closed` plan before any AI call.

- [ ] **Step 7: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_lead_conversion.py tests/test_messaging.py tests/test_browser_dm.py tests/test_browser_welcome.py -q
uv tool run ruff check src/tikpoc/lead_conversion.py src/tikpoc/browser_dm.py src/tikpoc/messaging.py tests/test_lead_conversion.py tests/test_browser_dm.py tests/test_messaging.py tests/test_browser_welcome.py
uv tool run ruff format --check src/tikpoc/lead_conversion.py src/tikpoc/browser_dm.py src/tikpoc/messaging.py tests/test_lead_conversion.py tests/test_browser_dm.py tests/test_messaging.py tests/test_browser_welcome.py
```

Expected: all focused tests, lint, and touched-file formatting pass.

Commit only the listed files with `feat: make browser customer service autonomous`.

### Task 2: Durable Stop-Contact Suppression

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/browser_dm.py`
- Modify: `src/tikpoc/browser_welcome.py`
- Modify: `tests/test_web_events_db.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_browser_dm.py`
- Modify: `tests/test_browser_welcome.py`

- [ ] **Step 1: Write failing database and service tests**

Add tests proving that closing a participant for explicit opt-out:

- supersedes that participant's pending `browser_welcome_plans` rows;
- makes future `plan_after_followback` calls return `None` for the same normalized account/username;
- does not suppress the same username on a different account;
- leaves already `sent` or `uncertain` welcome evidence unchanged;
- refuses a future follow-back claim for a suppressed participant while still
  allowing the same action key on another account;
- remains effective across a new `BrowserWelcomeService` instance.

```python
assert database.suppress_browser_contact(
    "account-01", "Buyer.One", reason="explicit_opt_out", now_ms=2_000
)
assert database.browser_contact_allowed("account-01", "buyer.one") is False
assert database.browser_contact_allowed("account-02", "buyer.one") is True
```

- [ ] **Step 2: Run the focused tests and observe missing database methods**

Run: `uv run pytest tests/test_web_events_db.py tests/test_dashboard_api.py tests/test_browser_dm.py tests/test_browser_welcome.py -q`

Expected: tests fail because durable participant suppression is not implemented.

- [ ] **Step 3: Add migration-safe suppression persistence**

Create `browser_contact_suppressions` during `Database.migrate()` with primary key `(account_id, participant_username)`, normalized username, reason, and `created_at_ms`. Implement:

```python
def suppress_browser_contact(
    self,
    account_id: str,
    participant_username: str,
    *,
    reason: str,
    now_ms: int,
) -> bool:
    username = str(participant_username).strip().removeprefix("@").casefold()
    if not account_id.strip() or not username or not reason.strip() or now_ms < 0:
        raise ValueError("invalid browser contact suppression")
    with self._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO browser_contact_suppressions(
                account_id, participant_username, reason, created_at_ms
            ) VALUES (?, ?, ?, ?)
            """,
            (account_id.strip(), username, reason.strip(), int(now_ms)),
        ).rowcount
        connection.execute(
            """
            UPDATE browser_welcome_plans
            SET state='superseded', updated_at_ms=?
            WHERE account_id=? AND follower_username=? AND state='planned'
            """,
            (int(now_ms), account_id.strip(), username),
        )
        return bool(inserted)

def browser_contact_allowed(
    self, account_id: str, participant_username: str
) -> bool:
    username = str(participant_username).strip().removeprefix("@").casefold()
    with self._connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM browser_contact_suppressions
            WHERE account_id=? AND participant_username=?
            """,
            (account_id.strip(), username),
        ).fetchone()
    return row is None
```

The write uses `BEGIN IMMEDIATE`, inserts idempotently, and changes only matching `planned` welcome rows to `superseded`.

- [ ] **Step 4: Connect suppression to DM and welcome services**

When `BrowserDmService.plan` receives `stop_contact_reason`, persist suppression before finalizing the closed reply plan. In `BrowserWelcomeService.plan_after_followback` and `next_plan`, skip suppressed participants without calling the AI provider or claiming a send lease. Add a database lookup from the account-scoped follower action key to the normalized username in its `new_follower` event; the FastAPI follow-back claim endpoint returns `claimed: false` when that username is suppressed.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
uv run pytest tests/test_web_events_db.py tests/test_dashboard_api.py tests/test_browser_dm.py tests/test_browser_welcome.py -q
uv tool run ruff check src/tikpoc/db.py src/tikpoc/api.py src/tikpoc/browser_dm.py src/tikpoc/browser_welcome.py tests/test_web_events_db.py tests/test_dashboard_api.py tests/test_browser_dm.py tests/test_browser_welcome.py
uv tool run ruff format --check src/tikpoc/db.py src/tikpoc/api.py src/tikpoc/browser_dm.py src/tikpoc/browser_welcome.py tests/test_web_events_db.py tests/test_dashboard_api.py tests/test_browser_dm.py tests/test_browser_welcome.py
```

Expected: suppression is account-scoped, durable, and all focused checks pass.

Commit only the listed files with `feat: persist browser stop-contact suppression`.

### Task 3: Route-Tolerant Continuous Browser Observation

**Files:**
- Modify: `chrome-event-bridge/manifest.json`
- Modify: `chrome-event-bridge/content.js`
- Modify: `chrome-event-bridge/content.test.js`
- Modify: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/dm-content.test.js`
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/db.py`
- Modify: `tests/test_dashboard_api.py`
- Modify: `tests/test_web_events_db.py`

- [ ] **Step 1: Write failing route and watchdog tests**

Add Node tests proving:

- the manifest injects the DM scripts into `https://www.tiktok.com/messages*` and `https://www.tiktok.com/business-suite/messages*` while the follower script excludes both;
- `pageRole` accepts root, trailing slash, query, and conversation subpaths for both families;
- a background health tick schedules a real serialized workflow scan for Activity and Messages;
- a 15-second watchdog schedules scans without a DOM mutation;
- `visibilitychange` and same-document path changes schedule scans;
- overlapping watchdog and mutation triggers never execute two workflow scans concurrently;
- a closed Activity panel can be reopened after the bounded reopen interval, but is not clicked repeatedly while visible.

- [ ] **Step 2: Run Node tests and observe route/watchdog failures**

Run:

```bash
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/content.test.js chrome-event-bridge/dm-content.test.js
```

Expected: Business Suite manifest coverage and watchdog scheduling assertions fail.

- [ ] **Step 3: Implement bounded watchdog scheduling**

Update the manifest with a second DM match for Business Suite. In both content scripts, keep the existing debounced scheduler and add one `setInterval(schedule, 15_000)` watchdog. A health tick must report health and then schedule a scan. Add a `visibilitychange` listener and compare the current pathname during watchdog execution to detect same-document route changes.

For Activity, replace the one-lifetime `activityOpenedByBridge` gate with visible-panel and elapsed-time checks so a closed panel can be reopened after 15 seconds without click loops. Keep `scanning`/promise-queue serialization and existing action leases unchanged.

- [ ] **Step 4: Write failing scan-health persistence tests**

Extend health payload tests with `last_scan_at_ms`, `last_success_at_ms`, and `scan_state`, without message content. Add migration/API tests proving older databases receive the columns and out-of-order heartbeats cannot move scan timestamps backwards.

```python
assert health == {
    "account_id": "account-01",
    "page_role": "messages",
    "last_scan_at_ms": 2_000,
    "last_success_at_ms": 2_000,
    "scan_state": "idle",
}
```

- [ ] **Step 5: Implement scan-health fields end to end**

Add bounded `scan_state` plus nonnegative scan timestamps to `BrowserHealthRequest`. Add migration-safe columns to `browser_account_health`, persist them in `upsert_browser_health`, and return them from `browser_health_snapshot`. Content scripts update in-memory scan state only after serialized scan completion and report it on the next health post. An exception advances `last_scan_at_ms` but not `last_success_at_ms`.

- [ ] **Step 6: Run focused browser and Python tests and commit**

Run:

```bash
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
uv run pytest tests/test_dashboard_api.py tests/test_web_events_db.py -q
uv tool run ruff check src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/db.py tests/test_dashboard_api.py tests/test_web_events_db.py
uv tool run ruff format --check src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/db.py tests/test_dashboard_api.py tests/test_web_events_db.py
```

Expected: all Chrome tests and focused Python tests pass.

Commit only the listed files with `fix: keep browser lead observers scanning`.

### Task 4: Regression, Runtime Enablement, And Live Acceptance

**Files:**
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Run the complete applicable automated regression**

Run:

```bash
uv run pytest -q
node --check chrome-event-bridge/*.js
node --test chrome-event-bridge/*.test.js
npm --prefix operator-console test -- --run
npm --prefix operator-console run build
bash android-event-bridge/build.sh
uv tool run ruff check src tests
git diff --check
```

Run touched-file Ruff format checks separately. Record the pre-existing full-repository format baseline without modifying unrelated files.

- [ ] **Step 2: Reload the unpacked extension and verify both routes read-only**

In each controlled Chrome Profile, reload `TikPoc Event Bridge`, reload TikTok, and verify `/messages` plus `/business-suite/messages` report a fresh `messages` scan heartbeat without sending. Open Activity and verify a fresh `activity` scan heartbeat. Do not record page text or screenshots containing personal data.

- [ ] **Step 3: Enable the account-scoped runtime controls**

After both Profiles have fresh baselines and `4/4` ready scan health, use the localhost account-control endpoints to enable AI reply and follow-back for both configured accounts. Confirm `/api/browser-bindings` returns `browser_dm_enabled=true` and `browser_followback_enabled=true` for both. Preserve secrets and ignored runtime settings.

- [ ] **Step 4: Perform controlled live acceptance**

Use two controlled message-capable accounts:

1. Create one fresh follow and verify exactly one visible follow-back.
2. Verify exactly one product-interest welcome appears and remains after reload.
3. Put the receiver Messages tab in the background, send one fresh natural-language inbound, and verify one language-matched AI reply appears.
4. Send a buying-interest message and verify the reply directs the customer to the profile link or pinned-post contact details without printing a stored direct destination.
5. Reload/rerender both sides and verify no duplicate follow-back, welcome, plan, lease, or visible reply.
6. Use a fresh synthetic stop-contact conversation and verify the server creates an empty closed plan with no visible outbound send.

If TikTok suppresses or removes a visible message, record the action `uncertain`, keep the lease busy, and use another controlled message-capable conversation. Do not count an HTTP response or DOM click as success.

- [ ] **Step 5: Update runbook and checkpoint**

Document the Chrome-running boundary, both route families, watchdog scan health, product-interest welcome, profile/pinned-post contact route, no live-transfer promise, stop-contact terminal behavior, and redacted live evidence. Update `AGENTS.md` with exact test counts and each passed/open live gate.

- [ ] **Step 6: Verify documentation and commit**

Run:

```bash
git diff --check
git status --short --branch
```

Commit only the implementation-owned documentation and any remaining task files with `docs: record autonomous browser customer service acceptance`.


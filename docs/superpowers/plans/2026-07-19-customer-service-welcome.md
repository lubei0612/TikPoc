# Customer-Service Reply And New-Follower Welcome Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable IKUN AI customer-service behavior and send one durable, visibly verified welcome DM after each successful browser follow-back.

**Architecture:** Extend the existing per-account runtime settings and prompt boundary, then add a small welcome-plan repository driven by completed follow-back evidence. The Messages content script claims at most one pending welcome when no inbound reply is waiting, targets an exact username through the visible UI, and reconciles the exact outbound bubble through existing lease patterns.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic, React/TypeScript, Manifest V3 JavaScript, pytest, Vitest, and Node's test runner.

---

### Task 1: Account Brand And Welcome Settings

**Files:**
- Modify: `src/tikpoc/runtime_settings.py`
- Modify: `src/tikpoc/web_accounts.py`
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/views/SettingsView.tsx`
- Modify: `tests/test_runtime_settings.py`
- Modify: `tests/test_settings_api.py`
- Modify: `operator-console/src/SettingsView.test.tsx`

- [ ] **Step 1: Write failing repository and API tests**

Add tests that save and reload `brand_name="Sample Brand"`,
`welcome_after_followback=True`, and `welcome_language="English"`; prove an
older settings document receives `""`, `False`, and `"English"`; and prove one
account update does not change the second account.

- [ ] **Step 2: Run the focused Python tests and verify missing-field failures**

Run: `uv run pytest tests/test_runtime_settings.py tests/test_settings_api.py -q`

Expected: failures show the three new fields are absent from
`AccountRuntimeSettings` and the settings API.

- [ ] **Step 3: Implement typed settings and runtime overlay**

Add these fields to `AccountRuntimeSettings` and
`AccountAutomationSettingsCommand`:

```python
brand_name: str = ""
welcome_after_followback: bool = False
welcome_language: str = "English"
```

Load string and boolean fields without coercing `False` to text, preserve
missing-field defaults, include them in account settings responses, add bounded
`brand_name` to `WebAccount`, and overlay it on the runtime account used by reply
planning.

- [ ] **Step 4: Run the focused Python tests and verify they pass**

Run: `uv run pytest tests/test_runtime_settings.py tests/test_settings_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Write failing console tests**

Extend the settings fixture and assert each account group renders `品牌名称`,
`默认欢迎语言`, and a `回关后发送欢迎私信` checkbox, then assert the saved JSON
contains values only for the selected account.

- [ ] **Step 6: Run the console test and verify the controls are missing**

Run: `npm test -- --run src/SettingsView.test.tsx`

Working directory: `operator-console`

Expected: the new labels are not found.

- [ ] **Step 7: Implement the settings controls**

Extend `AccountAutomationSettings` in `api.ts`, render a checkbox for the boolean
and bounded text inputs for brand and language, and preserve the current
account-scoped immutable state update pattern in `SettingsView.tsx`.

- [ ] **Step 8: Run focused console verification**

Run: `npm test -- --run src/SettingsView.test.tsx`

Expected: the settings tests pass.

- [ ] **Step 9: Commit the settings task**

```bash
git add src/tikpoc/runtime_settings.py src/tikpoc/web_accounts.py src/tikpoc/api_models.py src/tikpoc/api.py tests/test_runtime_settings.py tests/test_settings_api.py operator-console/src/api.ts operator-console/src/views/SettingsView.tsx operator-console/src/SettingsView.test.tsx
git commit -m "feat: configure brand customer service"
```

### Task 2: Warm Professional Customer-Service Prompt

**Files:**
- Modify: `src/tikpoc/web_accounts.py`
- Modify: `src/tikpoc/messaging.py`
- Modify: `src/tikpoc/browser_dm.py`
- Modify: `tests/test_messaging.py`
- Modify: `tests/test_browser_dm.py`

- [ ] **Step 1: Write failing prompt-contract tests**

Capture the provider request and assert the system prompt contains the four
service behaviors `acknowledge`, `assist`, `advance`, and `assure`; requires one
to three short sentences and at most one question; answers before qualifying;
uses configured facts only; and forbids immediate private-channel disclosure.

Add a first-turn test with `brand_name="Sample Brand"` that requires one AI
customer-service introduction, plus a history test with an outbound message
that requires the introduction not to be repeated.

- [ ] **Step 2: Run prompt tests and verify the rubric is absent**

Run: `uv run pytest tests/test_messaging.py tests/test_browser_dm.py -q`

Expected: new assertions fail against the abstract current prompt.

- [ ] **Step 3: Implement prompt and first-turn detection**

Add bounded `brand_name`, `introduce_ai`, `response_mode`, and
`welcome_language` parameters to `reply_conversation`. `response_mode` accepts
only `conversation` or `new_follower_welcome`; the welcome mode requires one
AI-service introduction, thanks the follower, uses the configured default
language, asks one product-oriented question, and forbids contact details.
Build ordinary replies around the four-part rubric, with AI disclosure only
when `introduce_ai` is true. In `BrowserDmService.plan`, derive first turn from
the absence of any prior outbound message in persisted history and pass the
runtime account brand.

Use this professional fallback intent when no provider response is available:

```text
Thank you for contacting us. I'm the AI customer-service assistant. Which product details would you like to know first?
```

The configured account fallback continues to take precedence.

- [ ] **Step 4: Run focused prompt tests and verify they pass**

Run: `uv run pytest tests/test_messaging.py tests/test_browser_dm.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit the prompt task**

```bash
git add src/tikpoc/web_accounts.py src/tikpoc/messaging.py src/tikpoc/browser_dm.py tests/test_messaging.py tests/test_browser_dm.py
git commit -m "feat: reply as a professional AI service agent"
```

### Task 3: Durable Welcome Plans From Completed Follow-Backs

**Files:**
- Modify: `src/tikpoc/db.py`
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Create: `src/tikpoc/browser_welcome.py`
- Modify: `src/tikpoc/messaging.py`
- Create: `tests/test_browser_welcome.py`
- Modify: `tests/test_messaging.py`
- Modify: `tests/test_dashboard_api.py`

- [ ] **Step 1: Write failing repository tests**

Create tests proving that only a completed `followback` result with a matching
`followback_completed` event creates a plan; unresolved and uncertain results
create none; repeated events/results create one plan per normalized account and
username; and equal usernames in two accounts remain isolated.

- [ ] **Step 2: Run repository tests and verify the welcome API is missing**

Run: `uv run pytest tests/test_browser_welcome.py tests/test_dashboard_api.py -q`

Expected: failures identify missing welcome schema, service, and endpoints.

- [ ] **Step 3: Implement schema and repository transitions**

Add migration-safe `browser_welcome_plans` storage with:

```text
id, account_id, follower_username, follower_key, reply_text,
state, created_at_ms, updated_at_ms
```

Use a unique index on normalized `(account_id, follower_username)`. Add methods
to find the matching completed follower event, insert or reuse a plan, claim the
oldest `planned` plan, and compare-and-set `sent|uncertain|superseded` results.

- [ ] **Step 4: Implement the welcome service and account-bound API**

`BrowserWelcomeService` loads runtime account settings, calls the existing AI
client in welcome mode with brand, language, offer, FAQ, and tone, and persists
the exact text before browser execution. Add:

```text
POST /api/browser-dm/welcome-plan
POST /api/browser-dm/welcome-result
```

Both requests carry the existing account/device/observed-username binding
identity. The plan response returns only plan ID, follower username, and stored
welcome text. Results accept only `sent`, `uncertain`, or `superseded`.

- [ ] **Step 5: Run focused repository and API tests**

Run: `uv run pytest tests/test_browser_welcome.py tests/test_dashboard_api.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the durable welcome task**

```bash
git add src/tikpoc/db.py src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/browser_welcome.py src/tikpoc/messaging.py tests/test_browser_welcome.py tests/test_messaging.py tests/test_dashboard_api.py
git commit -m "feat: plan one welcome after follow back"
```

### Task 4: Exact-Target Visible Welcome Send

**Files:**
- Modify: `chrome-event-bridge/background.js`
- Modify: `chrome-event-bridge/dm-core.js`
- Modify: `chrome-event-bridge/dm-content.js`
- Modify: `chrome-event-bridge/background.test.js`
- Modify: `chrome-event-bridge/dm-core.test.js`
- Modify: `chrome-event-bridge/dm-content.test.js`

- [ ] **Step 1: Write failing pure-helper and workflow tests**

Add tests for exact normalized username matching, ambiguity rejection, inbound
candidate priority, one welcome claim on idle scans, active-participant
verification before composing, `welcome_send` lease identity, exact outbound
confirmation, uncertain send handling, reload duplication prevention, and no
welcome workflow while Messages binding is unhealthy.

- [ ] **Step 2: Run Chrome tests and verify missing welcome transport/workflow**

Run: `node --test chrome-event-bridge/*.test.js`

Expected: new tests fail because welcome routes and exact-target workflow do not
exist.

- [ ] **Step 3: Add background transports**

Map `TIKPOC_WELCOME_PLAN` and `TIKPOC_WELCOME_RESULT` to the two server routes
using the same JSON and origin handling as current DM messages.

- [ ] **Step 4: Implement exact-target adapters and serialized welcome work**

Add semantic adapters for the visible new-conversation control, username search
input, exact username result, active participant, composer, and send button.
Reject zero or multiple exact matches. Extend the serialized scanner so inbound
reply handling returns first; only an idle scan requests one welcome plan. Claim
`welcome_send:<plan_id>`, open the exact participant, supersede when an existing
conversation already contains messages, compose and click once, and reconcile
the exact outbound bubble before reporting both plan and lease results.

- [ ] **Step 5: Run Chrome tests and verify they pass**

Run: `node --test chrome-event-bridge/*.test.js`

Expected: all Chrome extension tests pass.

- [ ] **Step 6: Commit the browser task**

```bash
git add chrome-event-bridge/background.js chrome-event-bridge/dm-core.js chrome-event-bridge/dm-content.js chrome-event-bridge/background.test.js chrome-event-bridge/dm-core.test.js chrome-event-bridge/dm-content.test.js
git commit -m "feat: send verified new-follower welcomes"
```

### Task 5: Documentation, Local Configuration, And Verification

**Files:**
- Modify: `docs/operator-console-runbook.md`
- Modify: `docs/web-engagement-runbook.md`
- Modify: `docs/superpowers/plans/2026-07-19-ai-private-channel-settings.md`
- Modify: `AGENTS.md`
- Local ignored update: `config/secrets/operator-settings.json`

- [ ] **Step 1: Update operator documentation**

Document the three fields, first-turn AI disclosure, default-language behavior,
completed-follow requirement, exact-target checks, welcome-plan states, and how
to pause welcome sends without disabling inbound replies.

- [ ] **Step 2: Apply local IKUN configuration without staging sensitive data**

Set both controlled accounts to brand `IKUN`, approved warm professional tone,
explicit welcome enablement, and the operator-selected default language in the
ignored owner-only settings file. Verify `git status` does not list the file and
do not print its contents.

- [ ] **Step 3: Run focused and complete automated verification**

Run:

```bash
uv run pytest tests/test_runtime_settings.py tests/test_settings_api.py tests/test_messaging.py tests/test_browser_dm.py tests/test_browser_welcome.py tests/test_dashboard_api.py -q
uv run pytest -q
node --test chrome-event-bridge/*.test.js
npm test -- --run
npm run build
uv tool run ruff check src tests
uv tool run ruff format --check src/tikpoc/runtime_settings.py src/tikpoc/api_models.py src/tikpoc/api.py src/tikpoc/messaging.py src/tikpoc/browser_dm.py src/tikpoc/browser_welcome.py tests/test_runtime_settings.py tests/test_settings_api.py tests/test_messaging.py tests/test_browser_dm.py tests/test_browser_welcome.py tests/test_dashboard_api.py
bash android-event-bridge/build.sh
git diff --check
```

Use `operator-console` as the working directory for the two npm commands.
Expected: focused and full suites pass; the production console build succeeds;
Ruff, Android build, and whitespace checks pass.

- [ ] **Step 4: Run controlled browser acceptance**

Confirm both Profiles report Activity and Messages ready. Use one fresh
controlled follower in each direction. Verify one visible follow-back and one
welcome send per account, exact participant, no duplicate after reload, and an
ordinary AI continuation after the recipient replies. Do not retain message
bodies or contact destinations in evidence.

- [ ] **Step 5: Update checkpoints with measured evidence**

Record automated counts and redacted live states in `AGENTS.md` and mark only
the gates actually observed in the current browser acceptance plan. Preserve
any uncertain or restricted result rather than claiming success.

- [ ] **Step 6: Commit documentation and checkpoint**

```bash
git add AGENTS.md docs/operator-console-runbook.md docs/web-engagement-runbook.md docs/superpowers/plans/2026-07-19-ai-private-channel-settings.md
git commit -m "docs: report customer service welcome acceptance"
```

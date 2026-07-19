# AI Provider And Private-Channel Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add localhost-managed AI provider and per-account private-channel settings, then complete idempotent automatic follow-back and concise AI reply acceptance for two Chrome Profiles.

**Architecture:** A focused runtime-settings repository atomically stores sensitive local values in an ignored owner-only JSON file and overlays the committed account registry at request time. FastAPI owns validation, redacted settings contracts, and provider tests; the existing browser DM service keeps immutable plans and leases while adding channel-preference-aware invitations. React adds a compact Settings workspace, and Supabase remains a documented future repository replacement.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, React 19, TypeScript, Vite, Vitest, Chrome Manifest V3, pytest.

---

### Task 1: Runtime Settings Repository And Dynamic Provider

**Files:**
- Create: `src/tikpoc/runtime_settings.py`
- Modify: `src/tikpoc/messaging.py`
- Modify: `src/tikpoc/api.py`
- Modify: `src/tikpoc/cli.py`
- Test: `tests/test_runtime_settings.py`
- Test: `tests/test_messaging.py`

- [ ] **Step 1: Write failing repository and dynamic-provider tests**

```python
def test_runtime_settings_write_atomically_with_owner_only_permissions(tmp_path):
    store = RuntimeSettingsStore(tmp_path / "operator-settings.json")
    store.save_provider(base_url="https://provider.example/v1", api_key="secret", model="model-a")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.provider().key_configured is True
    assert "secret" not in repr(store.provider())

def test_dynamic_client_loads_latest_provider_for_each_new_plan(tmp_path):
    store = RuntimeSettingsStore(tmp_path / "settings.json")
    client = RuntimeAiReplyClient(store.provider_credentials, opener=fake_opener)
    store.save_provider(base_url="https://provider.example/v1", api_key="secret", model="model-b")
    assert client.reply("hello") == "configured reply"
```

- [ ] **Step 2: Run focused tests and observe missing types**

Run: `uv run pytest tests/test_runtime_settings.py tests/test_messaging.py -q`  
Expected: FAIL because `RuntimeSettingsStore` and `RuntimeAiReplyClient` do not exist.

- [ ] **Step 3: Implement atomic local storage and reloadable client**

```python
@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    base_url: str
    api_key: str
    model: str

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)

@dataclass(frozen=True)
class AccountRuntimeSettings:
    whatsapp: str = ""
    telegram: str = ""
    offer_context: str = ""
    faq_context: str = ""
    reply_tone: str = ""

class RuntimeSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("runtime settings must be an object")
        return value

    def _write(self, value: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)

    def provider_credentials(self) -> ProviderCredentials:
        provider = self._read().get("provider", {})
        if not isinstance(provider, dict):
            provider = {}
        return ProviderCredentials(
            base_url=str(provider.get("base_url") or os.getenv("TKAUTO_LLM_BASE_URL") or ""),
            api_key=str(provider.get("api_key") or os.getenv("TKAUTO_LLM_API_KEY") or ""),
            model=str(provider.get("model") or os.getenv("TKAUTO_LLM_MODEL") or ""),
        )

    def save_provider(self, *, base_url: str, api_key: str | None, model: str, clear_key: bool = False) -> ProviderCredentials:
        document = self._read()
        current = self.provider_credentials()
        document["provider"] = {
            "base_url": base_url.rstrip("/"),
            "api_key": "" if clear_key else (api_key or current.api_key),
            "model": model.strip(),
        }
        self._write(document)
        return self.provider_credentials()

    def account_settings(self, account_id: str) -> AccountRuntimeSettings:
        accounts = self._read().get("accounts", {})
        value = accounts.get(account_id, {}) if isinstance(accounts, dict) else {}
        value = value if isinstance(value, dict) else {}
        return AccountRuntimeSettings(**{
            field: str(value.get(field) or "")
            for field in AccountRuntimeSettings.__dataclass_fields__
        })

    def save_account(self, account_id: str, settings: AccountRuntimeSettings) -> AccountRuntimeSettings:
        document = self._read()
        accounts = document.setdefault("accounts", {})
        if not isinstance(accounts, dict):
            raise ValueError("runtime account settings must be an object")
        accounts[account_id] = asdict(settings)
        self._write(document)
        return self.account_settings(account_id)

class RuntimeAiReplyClient:
    def __init__(self, credentials_loader, *, opener=urlopen) -> None:
        self._credentials_loader = credentials_loader
        self._opener = opener

    def reply_conversation(self, history, **kwargs):
        provider = self._credentials_loader()
        return AiReplyClient(base_url=provider.base_url, api_key=provider.api_key, model=provider.model, opener=self._opener).reply_conversation(history, **kwargs)
```

Write via a same-directory temporary file, `fsync`, `chmod(0o600)`, and `os.replace`. Merge saved provider values over existing environment compatibility values without logging either source.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_runtime_settings.py tests/test_messaging.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/runtime_settings.py src/tikpoc/messaging.py src/tikpoc/api.py src/tikpoc/cli.py tests/test_runtime_settings.py tests/test_messaging.py
git commit -m "feat: add secure runtime AI settings"
```

### Task 2: Preferred-Channel Conversation Policy

**Files:**
- Modify: `src/tikpoc/web_accounts.py`
- Modify: `src/tikpoc/lead_conversion.py`
- Modify: `src/tikpoc/browser_dm.py`
- Modify: `src/tikpoc/messaging.py`
- Test: `tests/test_web_accounts.py`
- Test: `tests/test_lead_conversion.py`
- Test: `tests/test_browser_dm.py`
- Test: `tests/test_messaging.py`

- [ ] **Step 1: Write failing preference, CTA, cooldown, and isolation tests**

```python
def test_invite_asks_preference_before_disclosing_destinations():
    result = build_private_channel_instruction(should_invite=True, preferred_channel="", destinations={"whatsapp": "CONTACT_A", "telegram": "CONTACT_B"})
    assert "WhatsApp or Telegram" in result
    assert "CONTACT_A" not in result and "CONTACT_B" not in result

def test_selected_channel_is_the_only_destination_in_prompt():
    result = build_private_channel_instruction(should_invite=True, preferred_channel="telegram", destinations={"whatsapp": "CONTACT_A", "telegram": "CONTACT_B"})
    assert "CONTACT_B" in result and "CONTACT_A" not in result

def test_follow_only_does_not_create_dm_plan(tmp_path):
    assert repository.reply_plan_count(account_id="account-01") == 0
```

- [ ] **Step 2: Run focused tests and observe policy failures**

Run: `uv run pytest tests/test_web_accounts.py tests/test_lead_conversion.py tests/test_browser_dm.py tests/test_messaging.py -q`  
Expected: FAIL because account channel fields and preference-aware instructions are absent.

- [ ] **Step 3: Implement per-account channel overlay and prompt rules**

```python
@dataclass(frozen=True)
class PrivateChannels:
    whatsapp: str = ""
    telegram: str = ""

def preferred_private_channel(text: str) -> str:
    normalized = text.casefold()
    if "whatsapp" in normalized: return "whatsapp"
    if "telegram" in normalized or re.search(r"\btg\b", normalized): return "telegram"
    return ""
```

The browser service loads an account overlay for every new plan. When invitation policy is due, it asks for preference if both channels exist and no preference is present; otherwise it injects exactly one selected destination and one concise purchase-oriented closing instruction. Preserve the existing 24-hour cooldown, immutable plan, 12-reply budget, contact capture, and human-takeover rules.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_web_accounts.py tests/test_lead_conversion.py tests/test_browser_dm.py tests/test_messaging.py -q`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/web_accounts.py src/tikpoc/lead_conversion.py src/tikpoc/browser_dm.py src/tikpoc/messaging.py tests/test_web_accounts.py tests/test_lead_conversion.py tests/test_browser_dm.py tests/test_messaging.py
git commit -m "feat: guide qualified leads to preferred channels"
```

### Task 3: Settings API And Management Workspace

**Files:**
- Modify: `src/tikpoc/api_models.py`
- Modify: `src/tikpoc/api.py`
- Modify: `operator-console/src/api.ts`
- Modify: `operator-console/src/App.tsx`
- Create: `operator-console/src/views/SettingsView.tsx`
- Create: `operator-console/src/SettingsView.test.tsx`
- Modify: `operator-console/src/styles.css`
- Test: `tests/test_settings_api.py`
- Modify: `tests/e2e/operator-console.spec.ts`

- [ ] **Step 1: Write failing redaction and UI tests**

```python
def test_settings_api_never_returns_provider_key(client):
    response = client.get("/api/settings").json()
    assert response["provider"]["key_configured"] is True
    assert "api_key" not in response["provider"]

def test_blank_key_preserves_existing_secret(client):
    client.post("/api/settings/provider", json={"base_url": "https://provider.example/v1", "api_key": "", "model": "model-a"})
    assert client.get("/api/settings").json()["provider"]["key_configured"] is True
```

```tsx
it("saves provider and account automation without rendering the saved key", async () => {
  render(<SettingsView />);
  await user.type(await screen.findByLabelText("API Key"), "secret-value");
  await user.click(screen.getByRole("button", { name: "保存 AI 配置" }));
  expect(screen.queryByDisplayValue("secret-value")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run focused tests and observe missing endpoints/view**

Run: `uv run pytest tests/test_settings_api.py -q && npm --prefix operator-console test -- SettingsView.test.tsx`  
Expected: FAIL because settings contracts and view do not exist.

- [ ] **Step 3: Implement localhost settings contracts and UI**

```python
class ProviderSettingsCommand(BaseModel):
    base_url: str
    api_key: str = ""
    model: str
    clear_key: bool = False

class AccountAutomationSettingsCommand(BaseModel):
    whatsapp: str = ""
    telegram: str = ""
    offer_context: str = ""
    faq_context: str = ""
    reply_tone: str = ""
```

Add `GET /api/settings`, `POST /api/settings/provider`, `POST /api/settings/provider/test`, and `POST /api/settings/accounts/{account_id}`. Apply loopback, same-origin, JSON media-type, bounded fields, HTTPS/loopback URL, registry-account, and redaction checks. Add the `/settings` tab and form controls using existing console visual conventions; retain account switches in their existing durable DB endpoints.

- [ ] **Step 4: Run focused API, component, and production-build tests**

Run: `uv run pytest tests/test_settings_api.py tests/test_lead_api.py -q`  
Expected: PASS.  
Run: `npm --prefix operator-console test`  
Expected: PASS.  
Run: `npm --prefix operator-console run build`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/tikpoc/api_models.py src/tikpoc/api.py operator-console/src/api.ts operator-console/src/App.tsx operator-console/src/views/SettingsView.tsx operator-console/src/SettingsView.test.tsx operator-console/src/styles.css tests/test_settings_api.py tests/e2e/operator-console.spec.ts src/tikpoc/static/console
git commit -m "feat: configure AI automation from the console"
```

### Task 4: Local Configuration, Regression, And Real Two-Account Acceptance

**Files:**
- Modify locally only: `config/secrets/operator-settings.json`
- Modify: `docs/web-engagement-runbook.md`
- Modify: `AGENTS.md`
- Modify in primary checkout: `/Users/chenyuqi/Desktop/tik/AGENTS.md`

- [ ] **Step 1: Save local provider and both account destinations through the settings service**

Use the already configured local provider values and user-supplied destinations. Confirm file mode `0600` and confirm `git status --ignored --short config/secrets/operator-settings.json` reports the file as ignored. Do not print its contents.

- [ ] **Step 2: Run full automated verification**

Run: `uv run pytest -q`  
Expected: all Python tests pass.  
Run: `node --test chrome-event-bridge/*.test.js`  
Expected: all Chrome tests pass.  
Run: `npm --prefix operator-console test && npm --prefix operator-console run build`  
Expected: component tests and production build pass.  
Run: `bash android-event-bridge/build.sh && uv tool run ruff check src tests && git diff --check`  
Expected: all commands pass.

- [ ] **Step 3: Start the localhost service and verify redacted readiness**

Run: `uv run tikpoc serve --db tikpoc.db --port 8766 --web-accounts config/web-accounts.yaml`  
Expected: `/api/settings` reports provider and both accounts configured without returning secrets, and Activity plus Messages health reaches `4/4` ready.

- [ ] **Step 4: Complete controlled visible follow-back acceptance**

Establish Activity baselines, enable follow-back for both accounts, trigger one controlled new follow, and verify exactly one leased visible follow-back. Reload the page and verify no second action is created.

- [ ] **Step 5: Complete controlled visible AI reply and private-channel acceptance**

Use a message-capable controlled conversation. Verify one inbound fingerprint creates one AI plan and one exclusive send lease; the visible reply arrives once and remains once after reload. Exercise a buying signal or second meaningful turn, verify the preference question, choose one channel, verify only that channel is disclosed, then verify the cooldown, contact-captured stage, and human takeover.

- [ ] **Step 6: Update redacted reports and commit**

Record test counts, browser health, account-scoped action states, and remaining gates without message text, contacts, destinations, keys, cookies, or tokens.

```bash
git add docs/web-engagement-runbook.md AGENTS.md
git commit -m "docs: report autonomous browser acceptance"
git -C /Users/chenyuqi/Desktop/tik add AGENTS.md
git -C /Users/chenyuqi/Desktop/tik commit -m "docs: report autonomous browser acceptance"
```

- [ ] **Step 7: Final verification**

Run: `git status --short --branch && git log -5 --oneline && git diff --check` in both checkouts.  
Expected: both worktrees are clean and the checkpoint commits are visible.

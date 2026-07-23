import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, repr=False)
class ProviderCredentials:
    base_url: str = ""
    api_key: str = ""
    model: str = ""

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
    brand_name: str = ""
    welcome_after_followback: bool = False
    welcome_language: str = "English"


class RuntimeSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read_unlocked(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("runtime settings must be an object")
        return value

    def _write_unlocked(self, value: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=True, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _environment_provider() -> ProviderCredentials:
        return ProviderCredentials(
            base_url=(
                os.getenv("TKAUTO_LLM_BASE_URL")
                or os.getenv("MODEL_MONITOR_LLM_BASE_URL")
                or ""
            ),
            api_key=(
                os.getenv("TKAUTO_LLM_API_KEY")
                or os.getenv("MODEL_MONITOR_LLM_API_KEY")
                or ""
            ),
            model=(
                os.getenv("TKAUTO_LLM_MODEL")
                or os.getenv("MODEL_MONITOR_LLM_MODEL")
                or ""
            ),
        )

    def provider_credentials(self) -> ProviderCredentials:
        with self._lock:
            provider = self._read_unlocked().get("provider", {})
        if not isinstance(provider, dict):
            provider = {}
        environment = self._environment_provider()
        return ProviderCredentials(
            base_url=str(
                provider["base_url"] if "base_url" in provider else environment.base_url
            ).rstrip("/"),
            api_key=str(
                provider["api_key"] if "api_key" in provider else environment.api_key
            ),
            model=str(
                provider["model"] if "model" in provider else environment.model
            ).strip(),
        )

    def save_provider(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        clear_key: bool = False,
    ) -> ProviderCredentials:
        with self._lock:
            document = self._read_unlocked()
            current = self.provider_credentials()
            document["provider"] = {
                "base_url": str(base_url).strip().rstrip("/"),
                "api_key": ""
                if clear_key
                else (str(api_key or "").strip() or current.api_key),
                "model": str(model).strip(),
            }
            self._write_unlocked(document)
        return self.provider_credentials()

    def account_settings(self, account_id: str) -> AccountRuntimeSettings:
        with self._lock:
            accounts = self._read_unlocked().get("accounts", {})
        value = accounts.get(account_id, {}) if isinstance(accounts, dict) else {}
        if not isinstance(value, dict):
            value = {}
        welcome_after_followback = value.get("welcome_after_followback", False)
        if not isinstance(welcome_after_followback, bool):
            welcome_after_followback = False
        return AccountRuntimeSettings(
            whatsapp=str(value.get("whatsapp") or ""),
            telegram=str(value.get("telegram") or ""),
            offer_context=str(value.get("offer_context") or ""),
            faq_context=str(value.get("faq_context") or ""),
            reply_tone=str(value.get("reply_tone") or ""),
            brand_name=str(value.get("brand_name") or ""),
            welcome_after_followback=welcome_after_followback,
            welcome_language=str(value.get("welcome_language") or "English"),
        )

    def save_account(
        self, account_id: str, settings: AccountRuntimeSettings
    ) -> AccountRuntimeSettings:
        with self._lock:
            document = self._read_unlocked()
            accounts = document.setdefault("accounts", {})
            if not isinstance(accounts, dict):
                raise ValueError("runtime account settings must be an object")
            accounts[str(account_id)] = asdict(settings)
            self._write_unlocked(document)
        return self.account_settings(account_id)

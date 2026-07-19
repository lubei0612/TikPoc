from dataclasses import dataclass
from pathlib import Path

import yaml


def _parse_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError(f"{field} must be a boolean")


@dataclass(frozen=True)
class WebAccount:
    account_id: str
    device_id: str
    business_id: str = ""
    token_file: Path | None = None
    private_channel_hint: str = ""
    enabled: bool = True
    mode: str | None = None
    offer_context: str = ""
    faq_text: str = ""
    reply_language: str = "auto"
    max_auto_replies: int = 12
    invite_after_meaningful_turns: int = 2
    fallback_acknowledgement: str = "Thanks for your message. What are you looking for?"
    browser_followback_enabled: bool = True
    browser_dm_enabled: bool = True
    expected_tiktok_username: str = ""
    browser_profile_label: str = ""

    def __post_init__(self) -> None:
        if self.mode is None:
            mode = "business" if self.business_id or self.token_file else "browser"
        elif isinstance(self.mode, str):
            mode = self.mode.strip().lower()
        else:
            raise ValueError("mode must be browser or business")
        if mode not in {"browser", "business"}:
            raise ValueError("mode must be browser or business")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(
            self,
            "expected_tiktok_username",
            str(self.expected_tiktok_username).strip().removeprefix("@"),
        )
        object.__setattr__(
            self, "browser_profile_label", str(self.browser_profile_label).strip()
        )


class WebAccountRegistry:
    def __init__(self, accounts: tuple[WebAccount, ...]) -> None:
        self.accounts = accounts
        self._validate_unique_browser_mappings(accounts)
        self._by_account_id = {account.account_id: account for account in accounts}
        self._by_business_id = {
            account.business_id: account for account in accounts if account.business_id
        }

    @staticmethod
    def _validate_unique_browser_mappings(accounts: tuple[WebAccount, ...]) -> None:
        fields = {
            "account_id": [account.account_id for account in accounts],
            "device_id": [account.device_id for account in accounts],
            "expected_tiktok_username": [
                account.expected_tiktok_username
                for account in accounts
                if account.expected_tiktok_username
            ],
        }
        for field, values in fields.items():
            normalized = [value.casefold() for value in values]
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"duplicate {field}")

    @classmethod
    def from_path(cls, path: Path) -> "WebAccountRegistry":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
            raise ValueError("web account config must contain an accounts list")

        accounts: list[WebAccount] = []
        seen_account_ids: set[str] = set()
        seen_business_ids: set[str] = set()
        for index, item in enumerate(raw["accounts"], start=1):
            if not isinstance(item, dict):
                raise ValueError(f"account entry {index} must be an object")
            account_id = str(item.get("account_id") or "").strip()
            device_id = str(item.get("device_id") or "").strip()
            business_id = str(item.get("business_id") or "").strip()
            token_file_text = str(item.get("token_file") or "").strip()
            if not account_id or not device_id:
                raise ValueError(
                    f"account entry {index} is missing account_id or device_id"
                )

            if "mode" in item:
                mode = str(item["mode"]).strip().lower()
            else:
                mode = "business" if business_id or token_file_text else "browser"
            if mode not in {"browser", "business"}:
                raise ValueError(
                    f"account entry {index} mode must be browser or business"
                )
            if mode == "business" and (not business_id or not token_file_text):
                raise ValueError(
                    f"account entry {index} business mode requires "
                    "business_id and token_file"
                )
            if account_id in seen_account_ids:
                raise ValueError(f"duplicate account_id: {account_id}")
            if business_id and business_id in seen_business_ids:
                raise ValueError(f"duplicate business_id: {business_id}")
            seen_account_ids.add(account_id)
            if business_id:
                seen_business_ids.add(business_id)

            token_file: Path | None = None
            if token_file_text:
                token_file = Path(token_file_text).expanduser()
                if not token_file.is_absolute():
                    token_file = path.parent / token_file

            faq_text = ""
            faq_file_text = str(item.get("faq_file") or "").strip()
            if faq_file_text:
                faq_file = Path(faq_file_text).expanduser()
                if not faq_file.is_absolute():
                    faq_file = path.parent / faq_file
                faq_text = faq_file.read_text(encoding="utf-8")

            max_auto_replies_value = item.get("max_auto_replies", 12)
            if max_auto_replies_value in (None, ""):
                max_auto_replies_value = 12
            invite_after_turns_value = item.get("invite_after_meaningful_turns", 2)
            if invite_after_turns_value in (None, ""):
                invite_after_turns_value = 2
            accounts.append(
                WebAccount(
                    account_id=account_id,
                    device_id=device_id,
                    business_id=business_id,
                    token_file=token_file,
                    mode=mode,
                    private_channel_hint=str(
                        item.get("private_channel_hint") or ""
                    ).strip(),
                    offer_context=str(item.get("offer_context") or "").strip(),
                    faq_text=faq_text,
                    reply_language=str(item.get("reply_language") or "auto").strip(),
                    max_auto_replies=max(1, int(max_auto_replies_value)),
                    invite_after_meaningful_turns=max(1, int(invite_after_turns_value)),
                    fallback_acknowledgement=str(
                        item.get("fallback_acknowledgement")
                        or "Thanks for your message. What are you looking for?"
                    ).strip(),
                    browser_followback_enabled=_parse_bool(
                        item.get("browser_followback_enabled", True),
                        field="browser_followback_enabled",
                    ),
                    browser_dm_enabled=_parse_bool(
                        item.get("browser_dm_enabled", True),
                        field="browser_dm_enabled",
                    ),
                    expected_tiktok_username=str(
                        item.get("expected_tiktok_username") or ""
                    ),
                    browser_profile_label=str(item.get("browser_profile_label") or ""),
                    enabled=_parse_bool(
                        item.get("enabled", True),
                        field="enabled",
                    ),
                )
            )
        return cls(tuple(accounts))

    def by_account_id(self, account_id: str) -> WebAccount:
        try:
            return self._by_account_id[account_id]
        except KeyError as error:
            raise KeyError(f"unknown web account: {account_id}") from error

    def by_business_id(self, business_id: str) -> WebAccount:
        try:
            return self._by_business_id[business_id]
        except KeyError as error:
            raise KeyError(f"unknown business account: {business_id}") from error

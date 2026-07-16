from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class WebAccount:
    account_id: str
    device_id: str
    business_id: str
    token_file: Path
    private_channel_hint: str = ""
    enabled: bool = True


class WebAccountRegistry:
    def __init__(self, accounts: tuple[WebAccount, ...]) -> None:
        self.accounts = accounts
        self._by_account_id = {account.account_id: account for account in accounts}
        self._by_business_id = {account.business_id: account for account in accounts}

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
            if not all((account_id, device_id, business_id, token_file_text)):
                raise ValueError(f"account entry {index} is missing required fields")
            if account_id in seen_account_ids:
                raise ValueError(f"duplicate account_id: {account_id}")
            if business_id in seen_business_ids:
                raise ValueError(f"duplicate business_id: {business_id}")
            seen_account_ids.add(account_id)
            seen_business_ids.add(business_id)

            token_file = Path(token_file_text).expanduser()
            if not token_file.is_absolute():
                token_file = path.parent / token_file
            accounts.append(
                WebAccount(
                    account_id=account_id,
                    device_id=device_id,
                    business_id=business_id,
                    token_file=token_file,
                    private_channel_hint=str(
                        item.get("private_channel_hint") or ""
                    ).strip(),
                    enabled=bool(item.get("enabled", True)),
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

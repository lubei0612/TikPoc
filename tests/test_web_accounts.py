import json
import stat
from pathlib import Path

import pytest

from tikpoc.business_messaging import BusinessToken, JsonTokenStore
from tikpoc.web_accounts import WebAccountRegistry


def test_web_account_registry_loads_and_resolves_relative_token_file(
    tmp_path: Path,
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    business_id: business-01\n"
        "    token_file: secrets/account-01.json\n"
        "    private_channel_hint: 'WhatsApp: example'\n",
        encoding="utf-8",
    )

    registry = WebAccountRegistry.from_path(config)
    account = registry.by_business_id("business-01")

    assert account.account_id == "account-01"
    assert account.device_id == "phone-01"
    assert account.token_file == tmp_path / "secrets" / "account-01.json"
    assert account.private_channel_hint == "WhatsApp: example"
    assert registry.by_account_id("account-01") == account


def test_web_account_registry_rejects_duplicate_identity(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    business_id: business-01\n"
        "    token_file: one.json\n"
        "  - account_id: account-01\n"
        "    device_id: phone-02\n"
        "    business_id: business-02\n"
        "    token_file: two.json\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate account_id"):
        WebAccountRegistry.from_path(config)


def test_json_token_store_round_trips_with_private_file_permissions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tokens" / "account-01.json"
    store = JsonTokenStore(path)
    token = BusinessToken(
        client_id="client-id",
        client_secret="client-secret",
        access_token="access-token",
        refresh_token="refresh-token",
        access_expires_at=2_000,
        refresh_expires_at=20_000,
        business_id="business-01",
    )

    store.save(token)

    assert store.load() == token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["access_token"] == "access-token"


def test_business_token_expiry_uses_refresh_skew() -> None:
    token = BusinessToken(
        client_id="client-id",
        client_secret="client-secret",
        access_token="access-token",
        refresh_token="refresh-token",
        access_expires_at=1_100,
        refresh_expires_at=2_000,
        business_id="business-01",
    )

    assert token.access_expired(now=1_000, skew_seconds=60) is False
    assert token.access_expired(now=1_041, skew_seconds=60) is True
    assert token.refresh_expired(now=2_001) is True

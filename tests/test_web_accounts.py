import json
import stat
from pathlib import Path

import pytest

from tikpoc.business_messaging import BusinessToken, JsonTokenStore
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


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
    assert account.mode == "business"
    assert account.token_file == tmp_path / "secrets" / "account-01.json"
    assert account.private_channel_hint == "WhatsApp: example"
    assert registry.by_account_id("account-01") == account


def test_browser_account_does_not_require_business_credentials(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    mode: browser\n"
        "    private_channel_hint: 'WhatsApp: +1 555 0100'\n"
        "    offer_context: Bags from the current catalog\n"
        "    faq_file: faq.md\n"
        "    reply_language: auto\n"
        "    max_auto_replies: 9\n"
        "    invite_after_meaningful_turns: 3\n"
        "    fallback_acknowledgement: Thanks for your message.\n"
        "    browser_followback_enabled: false\n"
        "    browser_dm_enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "faq.md").write_text("Shipping takes 5-7 days.", encoding="utf-8")

    account = WebAccountRegistry.from_path(config).by_account_id("account-01")

    assert account.mode == "browser"
    assert account.business_id == ""
    assert account.token_file is None
    assert account.private_channel_hint == "WhatsApp: +1 555 0100"
    assert account.offer_context == "Bags from the current catalog"
    assert account.faq_text == "Shipping takes 5-7 days."
    assert account.reply_language == "auto"
    assert account.max_auto_replies == 9
    assert account.invite_after_meaningful_turns == 3
    assert account.fallback_acknowledgement == "Thanks for your message."
    assert account.browser_followback_enabled is False
    assert account.browser_dm_enabled is True


def test_browser_account_loads_separate_private_channels_and_reply_tone(
    tmp_path: Path,
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    whatsapp: CONTACT_A\n"
        "    telegram: CHANNEL_A\n"
        "    reply_tone: Brief and practical\n",
        encoding="utf-8",
    )

    account = WebAccountRegistry.from_path(config).by_account_id("account-01")

    assert account.whatsapp == "CONTACT_A"
    assert account.telegram == "CHANNEL_A"
    assert account.reply_tone == "Brief and practical"


def test_quoted_false_disables_boolean_account_settings(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    browser_followback_enabled: 'false'\n"
        "    browser_dm_enabled: 'false'\n"
        "    enabled: 'false'\n",
        encoding="utf-8",
    )

    account = WebAccountRegistry.from_path(config).by_account_id("account-01")

    assert account.browser_followback_enabled is False
    assert account.browser_dm_enabled is False
    assert account.enabled is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("TrUe", True),
        ("FaLsE", False),
        ("YES", True),
        ("no", False),
        ("On", True),
        ("OFF", False),
        ("1", True),
        ("0", False),
    ],
)
def test_boolean_account_settings_accept_recognized_strings(
    tmp_path: Path, value: str, expected: bool
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        f"    browser_dm_enabled: '{value}'\n",
        encoding="utf-8",
    )

    account = WebAccountRegistry.from_path(config).by_account_id("account-01")

    assert account.browser_dm_enabled is expected


@pytest.mark.parametrize(
    "field",
    ["browser_followback_enabled", "browser_dm_enabled", "enabled"],
)
def test_invalid_boolean_account_settings_name_the_field(
    tmp_path: Path, field: str
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        f"    {field}: 'sometimes'\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        WebAccountRegistry.from_path(config)


@pytest.mark.parametrize(
    "value_yaml",
    ["1", "0", "[]", "{}", "null"],
    ids=["one", "zero", "list", "object", "null"],
)
def test_boolean_account_settings_reject_non_boolean_types(
    tmp_path: Path, value_yaml: str
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        f"    browser_dm_enabled: {value_yaml}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="browser_dm_enabled"):
        WebAccountRegistry.from_path(config)


def test_web_account_preserves_legacy_positional_constructor() -> None:
    token_file = Path("token")

    account = WebAccount("a", "d", "b", token_file, "hint", False)

    assert account.account_id == "a"
    assert account.device_id == "d"
    assert account.business_id == "b"
    assert account.token_file == token_file
    assert account.private_channel_hint == "hint"
    assert account.enabled is False
    assert account.mode == "business"


def test_web_account_example_loads_without_external_faq_file() -> None:
    config = Path(__file__).parents[1] / "config" / "web-accounts.example.yaml"

    registry = WebAccountRegistry.from_path(config)

    assert [account.account_id for account in registry.accounts] == [
        "account-01",
        "account-02",
    ]
    assert registry.by_account_id("account-01").faq_text == ""
    assert registry.by_account_id("account-02").enabled is False


def test_business_mode_still_requires_business_fields(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    mode: business\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="business_id and token_file"):
        WebAccountRegistry.from_path(config)


def test_web_account_registry_rejects_unknown_mode(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    mode: unsupported\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="browser or business"):
        WebAccountRegistry.from_path(config)


@pytest.mark.parametrize(
    "mode_yaml",
    ["false", "0", "[]", "''"],
    ids=["false", "zero", "empty-list", "empty-string"],
)
def test_web_account_registry_rejects_explicit_falsy_modes(
    tmp_path: Path, mode_yaml: str
) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        f"    mode: {mode_yaml}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="browser or business"):
        WebAccountRegistry.from_path(config)


def test_browser_conversion_integer_settings_have_minimums(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    max_auto_replies: 0\n"
        "    invite_after_meaningful_turns: -2\n",
        encoding="utf-8",
    )

    account = WebAccountRegistry.from_path(config).by_account_id("account-01")

    assert account.max_auto_replies == 1
    assert account.invite_after_meaningful_turns == 1


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


def test_duplicate_empty_business_ids_are_allowed(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "  - account_id: account-02\n"
        "    device_id: phone-02\n",
        encoding="utf-8",
    )

    registry = WebAccountRegistry.from_path(config)

    assert registry.by_account_id("account-01").browser_dm_enabled is True
    assert registry.by_account_id("account-02").browser_followback_enabled is True
    with pytest.raises(KeyError, match="unknown business account"):
        registry.by_business_id("")


def test_duplicate_nonempty_business_ids_are_rejected(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    business_id: business-01\n"
        "    token_file: one.json\n"
        "  - account_id: account-02\n"
        "    device_id: phone-02\n"
        "    business_id: business-01\n"
        "    token_file: two.json\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate business_id"):
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


def test_browser_registry_supports_arbitrary_account_counts(tmp_path: Path) -> None:
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        + "".join(
            f"  - account_id: account-{index:02d}\n"
            f"    device_id: phone-{index:02d}\n"
            f"    expected_tiktok_username: shop_{index:02d}\n"
            f"    browser_profile_label: TikPoc {index:02d}\n"
            for index in range(1, 13)
        ),
        encoding="utf-8",
    )
    registry = WebAccountRegistry.from_path(config)
    assert len(registry.accounts) == 12
    assert registry.accounts[-1].expected_tiktok_username == "shop_12"
    assert registry.accounts[-1].browser_profile_label == "TikPoc 12"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("account_id", "ACCOUNT-01", "duplicate account_id"),
        ("device_id", "PHONE-01", "duplicate device_id"),
        (
            "expected_tiktok_username",
            "@SHOP_ONE",
            "duplicate expected_tiktok_username",
        ),
    ],
)
def test_browser_registry_rejects_case_insensitive_mapping_duplicates(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    second = {
        "account_id": "account-02",
        "device_id": "phone-02",
        "expected_tiktok_username": "shop_two",
    }
    second[field] = value
    config = tmp_path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - account_id: account-01\n"
        "    device_id: phone-01\n"
        "    expected_tiktok_username: shop_one\n"
        f"  - account_id: {second['account_id']}\n"
        f"    device_id: {second['device_id']}\n"
        f'    expected_tiktok_username: "{second["expected_tiktok_username"]}"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        WebAccountRegistry.from_path(config)

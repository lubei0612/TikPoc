from pathlib import Path

from fastapi.testclient import TestClient

from tikpoc.api import create_app
from tikpoc.runtime_settings import AccountRuntimeSettings, RuntimeSettingsStore
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


def _settings_client(
    tmp_path: Path, *, provider_tester=None
) -> tuple[TestClient, RuntimeSettingsStore]:
    store = RuntimeSettingsStore(tmp_path / "secrets" / "operator-settings.json")
    store.save_provider(
        base_url="https://provider.example/v1",
        api_key="synthetic-secret",
        model="model-a",
    )
    store.save_account(
        "account-01",
        AccountRuntimeSettings(
            whatsapp="CONTACT_A",
            telegram="CHANNEL_A",
            offer_context="Synthetic offer",
            faq_context="Synthetic FAQ",
            reply_tone="Brief",
        ),
    )
    registry = WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                expected_tiktok_username="shop_one",
                browser_profile_label="Profile One",
            ),
            WebAccount(
                account_id="account-02",
                device_id="phone-02",
                expected_tiktok_username="shop_two",
                browser_profile_label="Profile Two",
            ),
        )
    )
    app = create_app(
        tmp_path / "tikpoc.db",
        registry=registry,
        runtime_settings=store,
        provider_tester=provider_tester,
    )
    return TestClient(app), store


def test_settings_api_returns_editable_accounts_without_provider_key(tmp_path) -> None:
    client, _ = _settings_client(tmp_path)

    response = client.get("/api/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == {
        "base_url": "https://provider.example/v1",
        "model": "model-a",
        "key_configured": True,
    }
    assert "api_key" not in str(payload["provider"])
    assert payload["accounts"][0] == {
        "account_id": "account-01",
        "browser_profile_label": "Profile One",
        "expected_tiktok_username": "shop_one",
        "whatsapp": "CONTACT_A",
        "telegram": "CHANNEL_A",
        "offer_context": "Synthetic offer",
        "faq_context": "Synthetic FAQ",
        "reply_tone": "Brief",
    }
    assert payload["accounts"][1]["whatsapp"] == ""


def test_blank_provider_key_preserves_existing_secret(tmp_path) -> None:
    client, store = _settings_client(tmp_path)

    response = client.post(
        "/api/settings/provider",
        headers={"Origin": "http://testserver"},
        json={
            "base_url": "https://provider-two.example/v1/",
            "api_key": "",
            "model": "model-b",
            "clear_key": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "base_url": "https://provider-two.example/v1",
        "model": "model-b",
        "key_configured": True,
    }
    assert store.provider_credentials().api_key == "synthetic-secret"


def test_invalid_provider_url_does_not_replace_working_configuration(tmp_path) -> None:
    client, store = _settings_client(tmp_path)

    response = client.post(
        "/api/settings/provider",
        headers={"Origin": "http://testserver"},
        json={"base_url": "http://remote.example/v1", "api_key": "", "model": "x"},
    )

    assert response.status_code == 422
    assert store.provider_credentials().base_url == "https://provider.example/v1"
    assert store.provider_credentials().model == "model-a"


def test_account_settings_save_is_isolated_and_unknown_account_is_rejected(
    tmp_path,
) -> None:
    client, store = _settings_client(tmp_path)
    payload = {
        "whatsapp": "CONTACT_B",
        "telegram": "CHANNEL_B",
        "offer_context": "Offer B",
        "faq_context": "FAQ B",
        "reply_tone": "Friendly and brief",
    }

    saved = client.post(
        "/api/settings/accounts/account-02",
        headers={"Origin": "http://testserver"},
        json=payload,
    )
    missing = client.post(
        "/api/settings/accounts/missing",
        headers={"Origin": "http://testserver"},
        json=payload,
    )

    assert saved.status_code == 200
    assert saved.json()["account_id"] == "account-02"
    assert store.account_settings("account-02").whatsapp == "CONTACT_B"
    assert store.account_settings("account-01").whatsapp == "CONTACT_A"
    assert missing.status_code == 404


def test_settings_mutations_require_json_and_same_origin(tmp_path) -> None:
    client, _ = _settings_client(tmp_path)
    body = '{"base_url":"https://provider.example/v1","api_key":"","model":"x"}'

    wrong_origin = client.post(
        "/api/settings/provider",
        headers={"Origin": "https://other.example", "Content-Type": "application/json"},
        content=body,
    )
    wrong_media = client.post(
        "/api/settings/provider",
        headers={"Origin": "http://testserver", "Content-Type": "text/plain"},
        content=body,
    )

    assert wrong_origin.status_code == 403
    assert wrong_media.status_code == 415


def test_provider_connection_test_returns_only_status_model_and_latency(
    tmp_path,
) -> None:
    seen = []

    def provider_tester(provider):
        seen.append(provider)
        return True, 37

    client, _ = _settings_client(tmp_path, provider_tester=provider_tester)

    response = client.post(
        "/api/settings/provider/test",
        headers={"Origin": "http://testserver"},
        json={},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "model": "model-a", "elapsed_ms": 37}
    assert seen[0].key_configured is True
    assert "synthetic-secret" not in response.text


def test_browser_dm_account_overlay_reads_latest_saved_account_settings(
    tmp_path,
) -> None:
    client, store = _settings_client(tmp_path)
    service = client.app.state.browser_dm_service

    initial = service._account("account-01", "phone-01")
    store.save_account(
        "account-01",
        AccountRuntimeSettings(
            whatsapp="CONTACT_UPDATED",
            telegram="CHANNEL_UPDATED",
            offer_context="Updated offer",
            faq_context="Updated FAQ",
            reply_tone="Updated tone",
        ),
    )
    updated = service._account("account-01", "phone-01")

    assert initial.whatsapp == "CONTACT_A"
    assert updated.whatsapp == "CONTACT_UPDATED"
    assert updated.offer_context == "Updated offer"

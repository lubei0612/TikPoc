import stat

from tikpoc.runtime_settings import AccountRuntimeSettings, RuntimeSettingsStore


def test_runtime_settings_write_atomically_with_owner_only_permissions(
    tmp_path,
) -> None:
    path = tmp_path / "secrets" / "operator-settings.json"
    store = RuntimeSettingsStore(path)

    saved = store.save_provider(
        base_url="https://provider.example/v1/",
        api_key="synthetic-secret",
        model="model-a",
    )

    assert path.exists()
    assert not path.with_name(f".{path.name}.tmp").exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert saved.base_url == "https://provider.example/v1"
    assert saved.model == "model-a"
    assert saved.key_configured is True
    assert "synthetic-secret" not in repr(saved)


def test_blank_provider_key_preserves_saved_value_and_explicit_clear_removes_it(
    tmp_path,
) -> None:
    store = RuntimeSettingsStore(tmp_path / "operator-settings.json")
    store.save_provider(
        base_url="https://provider.example/v1",
        api_key="synthetic-secret",
        model="model-a",
    )

    preserved = store.save_provider(
        base_url="https://provider.example/v1",
        api_key="",
        model="model-b",
    )
    cleared = store.save_provider(
        base_url="https://provider.example/v1",
        api_key="",
        model="model-b",
        clear_key=True,
    )

    assert preserved.api_key == "synthetic-secret"
    assert cleared.api_key == ""
    assert cleared.key_configured is False


def test_account_settings_are_isolated_and_round_trip(tmp_path) -> None:
    store = RuntimeSettingsStore(tmp_path / "operator-settings.json")
    first = AccountRuntimeSettings(
        whatsapp="CONTACT_A",
        telegram="CHANNEL_A",
        offer_context="Offer A",
        faq_context="FAQ A",
        reply_tone="Brief",
        brand_name="Sample Brand",
        welcome_after_followback=True,
        welcome_language="English",
    )

    store.save_account("account-01", first)

    assert store.account_settings("account-01") == first
    assert store.account_settings("account-02") == AccountRuntimeSettings()


def test_legacy_account_settings_receive_welcome_defaults(tmp_path) -> None:
    path = tmp_path / "operator-settings.json"
    path.write_text(
        '{"accounts":{"account-01":{"reply_tone":"Brief"}}}',
        encoding="utf-8",
    )

    settings = RuntimeSettingsStore(path).account_settings("account-01")

    assert settings.brand_name == ""
    assert settings.welcome_after_followback is False
    assert settings.welcome_language == "English"


def test_environment_is_used_only_when_saved_provider_field_is_missing(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("TKAUTO_LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("TKAUTO_LLM_API_KEY", "environment-secret")
    monkeypatch.setenv("TKAUTO_LLM_MODEL", "environment-model")
    store = RuntimeSettingsStore(tmp_path / "operator-settings.json")

    initial = store.provider_credentials()
    saved = store.save_provider(
        base_url="https://saved.example/v1",
        api_key="saved-secret",
        model="saved-model",
    )

    assert initial.base_url == "https://environment.example/v1"
    assert initial.model == "environment-model"
    assert saved.base_url == "https://saved.example/v1"
    assert saved.api_key == "saved-secret"
    assert saved.model == "saved-model"

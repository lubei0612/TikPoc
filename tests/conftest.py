import pytest


@pytest.fixture(autouse=True)
def configured_browser_extension_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TIKPOC_BROWSER_EXTENSION_ORIGINS",
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
    )

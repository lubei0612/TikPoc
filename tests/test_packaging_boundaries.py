import tomllib
from pathlib import Path


def test_production_install_does_not_require_legacy_appium_runtime() -> None:
    payload = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )

    dependencies = payload["project"]["dependencies"]
    legacy = payload["project"]["optional-dependencies"]["legacy-appium"]
    assert not any(value.startswith("Appium-Python-Client") for value in dependencies)
    assert any(value.startswith("Appium-Python-Client") for value in legacy)

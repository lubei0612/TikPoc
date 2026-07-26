import pytest

from tikpoc.navigation import NavigationMode


def test_navigation_mode_parses_supported_values() -> None:
    assert NavigationMode.parse("deeplink") is NavigationMode.DEEPLINK
    assert NavigationMode.parse(" search ") is NavigationMode.SEARCH


@pytest.mark.parametrize("value", ["", "Searches", "unknown", None])
def test_navigation_mode_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(ValueError, match="navigation mode"):
        NavigationMode.parse(value)

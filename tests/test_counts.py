import pytest

from tikpoc.counts import parse_visible_count


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1,234", 1234), ("1.2K", 1200), ("2M", 2_000_000), ("0", 0)],
)
def test_parse_visible_count(raw: str, expected: int) -> None:
    assert parse_visible_count(raw) == expected


def test_parse_visible_count_rejects_ambiguous_text() -> None:
    with pytest.raises(ValueError, match="unreadable count"):
        parse_visible_count("many")


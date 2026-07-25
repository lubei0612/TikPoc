from decimal import Decimal, InvalidOperation

_MULTIPLIERS = {
    "": 1,
    "K": 1_000,
    "万": 10_000,
    "M": 1_000_000,
    "亿": 100_000_000,
    "B": 1_000_000_000,
}


def parse_visible_count(raw: str) -> int:
    value = "".join(raw.upper().replace(",", "").split())
    suffix = value[-1] if value and value[-1] in _MULTIPLIERS else ""
    number = value[:-1] if suffix else value
    try:
        parsed = Decimal(number) * _MULTIPLIERS[suffix]
    except InvalidOperation as error:
        raise ValueError(f"unreadable count: {raw!r}") from error
    if parsed < 0 or parsed != parsed.to_integral_value():
        raise ValueError(f"unreadable count: {raw!r}")
    return int(parsed)

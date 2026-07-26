from enum import StrEnum


class NavigationMode(StrEnum):
    DEEPLINK = "deeplink"
    SEARCH = "search"

    @classmethod
    def parse(cls, value: object) -> "NavigationMode":
        normalized = value.strip().lower() if isinstance(value, str) else ""
        try:
            return cls(normalized)
        except ValueError as error:
            raise ValueError("unsupported navigation mode") from error

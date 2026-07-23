import json
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import urlopen

from .web_accounts import WebAccountRegistry

PAGE_ROLES = ("activity", "messages")


def dashboard_origin(value: str) -> str:
    parsed = urlparse(str(value).strip())
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("dashboard URL must use loopback HTTP")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("dashboard URL must be an origin")
    return f"http://{parsed.netloc}"


def fetch_json(url: str) -> dict[str, object]:
    with urlopen(url, timeout=5) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("dashboard response must be an object")
    return payload


def redacted_browser_status(
    payload: dict[str, object], *, now_ms: int | None = None
) -> list[dict[str, object]]:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    rows = payload.get("browser_health")
    if not isinstance(rows, list):
        return []
    redacted = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        observed_at_ms = int(row.get("observed_at_ms") or 0)
        redacted.append(
            {
                "account_id": str(row.get("account_id") or ""),
                "browser_profile_label": str(row.get("browser_profile_label") or ""),
                "expected_tiktok_username": str(
                    row.get("expected_tiktok_username") or ""
                ),
                "observed_username": str(row.get("observed_username") or ""),
                "page_role": str(row.get("page_role") or ""),
                "binding_state": str(row.get("binding_state") or "unbound"),
                "heartbeat_age_ms": (
                    max(0, current_ms - observed_at_ms) if observed_at_ms else None
                ),
            }
        )
    return sorted(redacted, key=lambda row: (row["account_id"], row["page_role"]))


def _enabled_browser_accounts(registry: WebAccountRegistry):
    return tuple(
        account
        for account in registry.accounts
        if account.enabled and account.mode == "browser"
    )


def _validate_server_bindings(
    registry: WebAccountRegistry, payload: dict[str, object]
) -> None:
    rows = payload.get("accounts")
    if not isinstance(rows, list):
        raise ValueError("browser binding response is missing accounts")
    server = {
        str(row.get("account_id") or ""): (
            str(row.get("device_id") or ""),
            str(row.get("expected_tiktok_username") or "").casefold(),
        )
        for row in rows
        if isinstance(row, dict) and row.get("enabled") is True
    }
    configured = {
        account.account_id: (
            account.device_id,
            account.expected_tiktok_username.casefold(),
        )
        for account in _enabled_browser_accounts(registry)
    }
    if server != configured:
        raise ValueError("server browser bindings do not match the account registry")


def wait_for_browser_health(
    registry: WebAccountRegistry,
    dashboard_url: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, int, list[dict[str, object]]]:
    origin = dashboard_origin(dashboard_url)
    _validate_server_bindings(
        registry,
        fetch_json(f"{origin}/api/browser-bindings"),
    )
    accounts = _enabled_browser_accounts(registry)
    expected = {
        (account.account_id, role): account.expected_tiktok_username.casefold()
        for account in accounts
        for role in PAGE_ROLES
    }
    deadline = monotonic() + max(0.0, timeout_seconds)
    while True:
        rows = redacted_browser_status(fetch_json(f"{origin}/api/leads"))
        ready = {
            (str(row["account_id"]), str(row["page_role"]))
            for row in rows
            if row["binding_state"] == "ready"
            and str(row["observed_username"]).casefold()
            == expected.get((str(row["account_id"]), str(row["page_role"])), "")
        }
        ready_count = len(ready & set(expected))
        if ready_count == len(expected) or monotonic() >= deadline:
            return ready_count, len(expected), rows
        sleep(max(0.0, poll_interval_seconds))


def default_extension_path() -> Path:
    return Path(__file__).resolve().parents[2] / "chrome-event-bridge"

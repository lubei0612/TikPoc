import subprocess
from pathlib import Path

import pytest

from tikpoc.fleet import FleetConfig, FleetDevice
from tikpoc.proxy_guard import ProxyGuard


def _config() -> FleetConfig:
    return FleetConfig(
        myt_host="192.0.2.10",
        myt_sdk_port=8000,
        relay_bind_host="192.0.2.20",
        relay_bind_port=7898,
        relay_upstream_host="127.0.0.1",
        relay_upstream_port=7897,
        relay_allowed_sources=frozenset({"192.0.2.10"}),
        devices=(
            FleetDevice(
                device_id="phone-01",
                account_id="account-01",
                myt_slot=1,
                adb_endpoint="192.0.2.10:30000",
                appium_url="http://127.0.0.1:4723",
                order_seed="seed-01",
            ),
            FleetDevice(
                device_id="phone-02",
                account_id="account-02",
                myt_slot=2,
                adb_endpoint="192.0.2.10:30100",
                appium_url="http://127.0.0.1:4723",
                order_seed="seed-02",
            ),
        ),
    )


class FakeRunner:
    def __init__(self) -> None:
        self.proxy = {
            "192.0.2.10:30000": ("192.0.2.20:7897", "192.0.2.20", "7897"),
            "192.0.2.10:30100": ("192.0.2.99:7897", "192.0.2.99", "7897"),
        }
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs):
        command = tuple(command)
        self.commands.append(command)
        if command[1] == "connect":
            return subprocess.CompletedProcess(command, 0, "connected\n", "")
        endpoint = command[2]
        if command[3:] == ("get-state",):
            return subprocess.CompletedProcess(command, 0, "device\n", "")
        shell = command[3:]
        if shell[:4] == ("shell", "settings", "get", "global"):
            field = shell[4]
            index = {
                "http_proxy": 0,
                "global_http_proxy_host": 1,
                "global_http_proxy_port": 2,
            }[field]
            return subprocess.CompletedProcess(
                command, 0, self.proxy[endpoint][index] + "\n", ""
            )
        if shell[:4] == ("shell", "settings", "put", "global"):
            field, value = shell[4:]
            values = list(self.proxy[endpoint])
            index = {
                "http_proxy": 0,
                "global_http_proxy_host": 1,
                "global_http_proxy_port": 2,
            }[field]
            values[index] = value
            self.proxy[endpoint] = tuple(values)
            return subprocess.CompletedProcess(command, 0, "", "")
        if shell and shell[0] == "shell" and "curl" in shell:
            return subprocess.CompletedProcess(command, 0, "200", "")
        raise AssertionError(f"unexpected command: {command}")


def test_proxy_guard_only_corrects_devices_with_a_stale_lan_address() -> None:
    runner = FakeRunner()
    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=runner,
    )

    rows = guard.reconcile()

    assert [row.proxy_state for row in rows] == ["healthy", "corrected"]
    assert [row.http_status for row in rows] == [200, 200]
    writes = [command for command in runner.commands if "put" in command]
    assert len(writes) == 3
    assert all(command[2] == "192.0.2.10:30100" for command in writes)
    assert runner.proxy["192.0.2.10:30100"] == (
        "192.0.2.20:7897",
        "192.0.2.20",
        "7897",
    )
    assert all("subscription" not in repr(row).lower() for row in rows)


def test_proxy_guard_repeated_healthy_cycles_are_idempotent() -> None:
    runner = FakeRunner()
    runner.proxy["192.0.2.10:30100"] = runner.proxy["192.0.2.10:30000"]
    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=runner,
    )

    assert all(row.proxy_state == "healthy" for row in guard.reconcile())
    assert all(row.proxy_state == "healthy" for row in guard.reconcile())
    assert not any("put" in command for command in runner.commands)


def test_proxy_guard_opens_clash_once_when_listener_recovers() -> None:
    runner = FakeRunner()
    listener_results = iter((False, True))
    sleeps = []
    opened = []

    def recovery_runner(command, **kwargs):
        if tuple(command) == ("open", "-gja", "-a", "Clash Verge"):
            opened.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, "", "")
        return runner(command, **kwargs)

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: next(listener_results),
        runner=recovery_runner,
        sleeper=sleeps.append,
        recovery_wait_seconds=3,
    )

    rows = guard.reconcile()

    assert len(opened) == 1
    assert sleeps == [3]
    assert [row.proxy_state for row in rows] == ["healthy", "corrected"]


def test_proxy_guard_isolates_device_errors_without_exposing_stderr() -> None:
    runner = FakeRunner()

    def failing_runner(command, **kwargs):
        if len(command) > 2 and command[2] == "192.0.2.10:30000":
            raise subprocess.CalledProcessError(
                1, command, stderr="https://subscription.example/private-token"
            )
        return runner(command, **kwargs)

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=failing_runner,
    )

    rows = guard.reconcile()

    assert rows[0].proxy_state == "device_unavailable"
    assert rows[1].proxy_state == "corrected"
    assert "private-token" not in repr(rows)


def test_proxy_guard_records_source_address_outage_without_running_commands() -> None:
    runner = FakeRunner()

    def unavailable_source(_host):
        raise OSError("private interface detail")

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=unavailable_source,
        listener_probe=lambda _host, _port: True,
        runner=runner,
    )

    rows = guard.reconcile()

    assert [row.proxy_state for row in rows] == [
        "source_unavailable",
        "source_unavailable",
    ]
    assert runner.commands == []
    assert "private interface detail" not in repr(rows)


@pytest.mark.parametrize(
    ("curl_result", "expected_state", "expected_status"),
    [
        (subprocess.CompletedProcess((), 0, "403", ""), "failed", 403),
        (subprocess.CompletedProcess((), 0, "000", ""), "failed", 0),
        (subprocess.TimeoutExpired((), 12), "failed", None),
        (subprocess.CalledProcessError(127, ()), "unknown", None),
    ],
)
def test_proxy_guard_distinguishes_http_failure_from_missing_curl(
    curl_result, expected_state, expected_status
) -> None:
    runner = FakeRunner()
    runner.proxy["192.0.2.10:30100"] = runner.proxy["192.0.2.10:30000"]

    def probe_runner(command, **kwargs):
        if "curl" in command:
            if isinstance(curl_result, BaseException):
                raise curl_result
            return subprocess.CompletedProcess(
                command,
                curl_result.returncode,
                curl_result.stdout,
                curl_result.stderr,
            )
        return runner(command, **kwargs)

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=probe_runner,
        clock_ms=lambda: 123456789,
    )

    rows = guard.reconcile()

    assert rows[0].http_state == expected_state
    assert rows[0].http_status == expected_status
    assert rows[0].observed_at_ms == 123456789

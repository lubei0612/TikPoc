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
        if shell == (
            "shell",
            "settings",
            "get",
            "secure",
            "always_on_vpn_app",
        ):
            return subprocess.CompletedProcess(command, 0, "null\n", "")
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


class VpnRunner(FakeRunner):
    package = "com.github.metacubex.clash.meta"

    def __init__(self, *, running: bool = True) -> None:
        super().__init__()
        self.connectivity = (
            "NetworkAgentInfo{network{101} ni{VPN CONNECTED} "
            "lp{{InterfaceName: tun0}} nc{Capabilities: INTERNET&VALIDATED}}"
        )
        self.running = {
            "192.0.2.10:30000": running,
            "192.0.2.10:30100": True,
        }

    def __call__(self, command, **kwargs):
        command = tuple(command)
        self.commands.append(command)
        if command[1] == "connect":
            return subprocess.CompletedProcess(command, 0, "connected\n", "")
        endpoint = command[2]
        if command[3:] == ("get-state",):
            return subprocess.CompletedProcess(command, 0, "device\n", "")
        shell = command[3:]
        if shell == (
            "shell",
            "settings",
            "get",
            "secure",
            "always_on_vpn_app",
        ):
            return subprocess.CompletedProcess(command, 0, self.package + "\n", "")
        if shell == ("shell", "ps", "-A"):
            output = (
                f"u0_a1 123 1 {self.package}:background\n"
                if self.running[endpoint]
                else ""
            )
            return subprocess.CompletedProcess(command, 0, output, "")
        if shell == ("shell", "ip", "link", "show", "tun0"):
            if not self.running[endpoint]:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0, "9: tun0: <UP>\n", "")
        if shell == ("shell", "dumpsys", "connectivity"):
            return subprocess.CompletedProcess(command, 0, self.connectivity, "")
        if shell == (
            "shell",
            "am",
            "start",
            "-a",
            "com.github.metacubex.clash.meta.action.START_CLASH",
            "-n",
            "com.github.metacubex.clash.meta/com.github.kr328.clash.ExternalControlActivity",
        ):
            self.running[endpoint] = True
            return subprocess.CompletedProcess(command, 0, "Starting\n", "")
        if shell and shell[0] == "shell" and "curl" in shell:
            assert "-x" not in shell
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


def test_proxy_guard_uses_device_vpn_without_rewriting_global_proxy() -> None:
    runner = VpnRunner()

    def unavailable_source(_host):
        raise OSError("host relay is intentionally unavailable")

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=unavailable_source,
        listener_probe=lambda _host, _port: False,
        runner=runner,
    )

    rows = guard.reconcile()

    assert [row.proxy_state for row in rows] == ["vpn_healthy", "vpn_healthy"]
    assert [row.http_state for row in rows] == ["unknown", "unknown"]
    assert not any("put" in command for command in runner.commands)


def test_proxy_guard_recovers_a_stopped_clash_vpn_once() -> None:
    runner = VpnRunner(running=False)
    sleeps = []
    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=runner,
        sleeper=sleeps.append,
        recovery_wait_seconds=3,
    )

    rows = guard.reconcile()

    assert rows[0].proxy_state == "vpn_recovered"
    assert rows[0].http_state == "unknown"
    starts = [
        command
        for command in runner.commands
        if any("START_CLASH" in part for part in command)
    ]
    assert len(starts) == 1
    assert sleeps == [3]


def test_proxy_guard_does_not_borrow_validation_from_another_network() -> None:
    runner = VpnRunner()
    runner.connectivity = (
        "NetworkAgentInfo{network{101} ni{VPN CONNECTED} "
        "lp{{InterfaceName: tun0}} nc{Capabilities: INTERNET}} "
        "NetworkAgentInfo{network{102} ni{WIFI CONNECTED} "
        "lp{{InterfaceName: wlan0}} nc{Capabilities: INTERNET&VALIDATED}}"
    )
    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    rows = guard.reconcile()

    assert [row.proxy_state for row in rows] == [
        "vpn_unavailable",
        "vpn_unavailable",
    ]


def test_proxy_guard_uses_a_device_specific_proxy_port() -> None:
    config = _config()
    devices = (
        config.devices[0],
        FleetDevice(
            device_id="phone-02",
            account_id="account-02",
            myt_slot=2,
            adb_endpoint="192.0.2.10:30100",
            appium_url="http://127.0.0.1:4723",
            order_seed="seed-02",
            proxy_port=7899,
        ),
    )
    runner = FakeRunner()
    guard = ProxyGuard(
        FleetConfig(
            myt_host=config.myt_host,
            myt_sdk_port=config.myt_sdk_port,
            relay_bind_host=config.relay_bind_host,
            relay_bind_port=config.relay_bind_port,
            relay_upstream_host=config.relay_upstream_host,
            relay_upstream_port=config.relay_upstream_port,
            relay_allowed_sources=config.relay_allowed_sources,
            devices=devices,
        ),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=runner,
    )

    rows = guard.reconcile()

    assert [row.proxy_state for row in rows] == ["healthy", "corrected"]
    assert runner.proxy["192.0.2.10:30100"] == (
        "192.0.2.20:7899",
        "192.0.2.20",
        "7899",
    )
    slot_two_probe = next(
        command
        for command in runner.commands
        if command[2] == "192.0.2.10:30100" and "curl" in command
    )
    assert "http://192.0.2.20:7899" in slot_two_probe


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
    assert not any("put" in command or "curl" in command for command in runner.commands)
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


def test_proxy_guard_retries_one_transient_http_probe_failure() -> None:
    runner = FakeRunner()
    runner.proxy["192.0.2.10:30100"] = runner.proxy["192.0.2.10:30000"]
    attempts = 0

    def transient_runner(command, **kwargs):
        nonlocal attempts
        if "curl" in command:
            attempts += 1
            if attempts == 1:
                raise subprocess.CalledProcessError(35, command)
        return runner(command, **kwargs)

    guard = ProxyGuard(
        _config(),
        adb_path=Path("/sdk/adb"),
        source_address=lambda _host: "192.0.2.20",
        listener_probe=lambda _host, _port: True,
        runner=transient_runner,
    )

    rows = guard.reconcile()

    assert rows[0].http_state == "ok"
    assert rows[0].http_status == 200
    assert attempts == 3

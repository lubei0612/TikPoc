from pathlib import Path
import sqlite3
import threading
import time

import pytest
import yaml

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.fleet import (
    DeviceWorkerFence,
    DeviceWorkerLeaseLost,
    FleetConfig,
    FleetSupervisor,
    FleetWorkerState,
)


def _write_config(tmp_path: Path, devices: str) -> Path:
    path = tmp_path / "devices.yaml"
    path.write_text(
        """
myt:
  host: 192.168.28.114
  sdk_port: 8000
proxy_relay:
  bind_host: 192.168.28.144
  bind_port: 7898
  upstream_host: 127.0.0.1
  upstream_port: 7897
devices:
"""
        + devices,
        encoding="utf-8",
    )
    return path


def test_fleet_requires_unique_device_account_slot_endpoint_and_seed(
    tmp_path: Path,
) -> None:
    path = _write_config(
        tmp_path,
        """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: duplicate
  - device_id: phone-02
    account_id: account-02
    myt_slot: 2
    adb_endpoint: 192.168.28.114:30100
    appium_url: http://127.0.0.1:4723
    order_seed: duplicate
""",
    )

    with pytest.raises(ValueError, match="duplicate order seed"):
        FleetConfig.from_path(path)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("device_id", "duplicate device id"),
        ("account_id", "duplicate account id"),
        ("myt_slot", "duplicate MYT slot"),
        ("adb_endpoint", "duplicate ADB endpoint"),
    ),
)
def test_fleet_rejects_each_duplicate_mapping_field(
    tmp_path: Path, field: str, message: str
) -> None:
    path = _write_config(
        tmp_path,
        """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
  - device_id: phone-02
    account_id: account-02
    myt_slot: 2
    adb_endpoint: 192.168.28.114:30100
    appium_url: http://127.0.0.1:4723
    order_seed: seed-b
""",
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["devices"][1][field] = payload["devices"][0][field]
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        FleetConfig.from_path(path)


def test_fleet_rejects_a_non_mapping_device_entry(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
  - malformed-device-entry
""",
    )

    with pytest.raises(ValueError, match="device entry must be a mapping"):
        FleetConfig.from_path(path)


@pytest.mark.parametrize(
    ("path_parts", "value", "message"),
    (
        (("myt", "sdk_port"), 65_536, "MYT SDK port"),
        (("proxy_relay", "bind_host"), "", "relay bind host"),
        (("proxy_relay", "bind_port"), -1, "relay bind port"),
        (("proxy_relay", "upstream_port"), 65_536, "relay upstream port"),
        (("devices", 0, "myt_slot"), -1, "MYT slot"),
        (("myt", "host"), "not-an-ip", "MYT host"),
        (("proxy_relay", "bind_host"), "not-an-ip", "relay bind host"),
        (("proxy_relay", "upstream_host"), "192.0.2.30", "loopback"),
        (("devices", 0, "adb_endpoint"), "bad-endpoint", "ADB endpoint"),
        (("devices", 0, "appium_url"), "bad-url", "Appium URL"),
    ),
)
def test_fleet_rejects_invalid_hosts_ports_and_slots(
    tmp_path: Path, path_parts: tuple[object, ...], value: object, message: str
) -> None:
    path = _write_config(
        tmp_path,
        """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
""",
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    cursor = payload
    for part in path_parts[:-1]:
        cursor = cursor[part]
    cursor[path_parts[-1]] = value
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        FleetConfig.from_path(path)


def test_fleet_parses_two_myt_devices(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
  - device_id: phone-02
    account_id: account-02
    myt_slot: 2
    adb_endpoint: 192.168.28.114:30100
    appium_url: http://127.0.0.1:4723
    order_seed: seed-b
""",
    )

    config = FleetConfig.from_path(path)

    assert config.myt_host == "192.168.28.114"
    assert [device.device_id for device in config.devices] == ["phone-01", "phone-02"]
    assert config.relay_bind_port == 7898
    assert config.relay_allowed_sources == frozenset({"192.168.28.114"})


def test_device_worker_lease_is_exclusive_until_expiry(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()

    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=100
    )
    assert not repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_099, ttl_ms=100
    )
    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_100, ttl_ms=100
    )


def test_device_worker_lease_prevents_account_reuse_on_another_device(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()

    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=100
    )
    assert not repository.claim_device_worker_lease(
        "phone-02", "account-01", "worker-2", now_ms=1_001, ttl_ms=100
    )


def test_device_worker_lease_can_be_renewed_and_released(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    fence_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(fence_token, int)

    assert (
        repository.renew_device_worker_lease(
            "phone-01",
            "account-01",
            "worker-1",
            now_ms=1_050,
            ttl_ms=100,
            fence_token=fence_token,
        )
        == 1_150
    )
    assert not repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_100, ttl_ms=100
    )
    repository.release_device_worker_lease(
        "phone-01", "account-01", "worker-1", fence_token=fence_token
    )
    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_101, ttl_ms=100
    )


def test_same_owner_stale_token_cannot_mutate_replacement_lease_or_health(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    first_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "same-owner", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(first_token, int)
    assert repository.record_fleet_device_health(
        "phone-01",
        "account-01",
        "healthy",
        now_ms=1_001,
        owner_id="same-owner",
        fence_token=first_token,
        require_active_lease=True,
    )
    replacement_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "same-owner", now_ms=1_100, ttl_ms=100
    )
    assert isinstance(replacement_token, int)
    assert repository.record_fleet_device_health(
        "phone-01",
        "account-01",
        "healthy",
        now_ms=1_101,
        owner_id="same-owner",
        fence_token=replacement_token,
        require_active_lease=True,
    )

    with pytest.raises(ValueError, match="not active"):
        repository.renew_device_worker_lease(
            "phone-01",
            "account-01",
            "same-owner",
            now_ms=1_102,
            ttl_ms=100,
            fence_token=first_token,
        )
    with pytest.raises(ValueError, match="does not match"):
        repository.release_device_worker_lease(
            "phone-01",
            "account-01",
            "same-owner",
            fence_token=first_token,
        )
    assert not repository.record_fleet_device_health(
        "phone-01",
        "account-01",
        "unhealthy",
        now_ms=1_102,
        owner_id="same-owner",
        fence_token=first_token,
        expected_owner_id="same-owner",
        expected_fence_token=first_token,
    )
    stored = repository.fleet_device_health("phone-01")
    assert stored is not None
    assert stored["fence_token"] == replacement_token


def test_stale_device_worker_fence_blocks_a_side_effect(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    first_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(first_token, int)
    fence = DeviceWorkerFence(
        database_path=repository.path,
        device_id="phone-01",
        account_id="account-01",
        owner_id="worker-1",
        fence_token=first_token,
    )
    side_effects: list[str] = []
    fence.execute(side_effects.append, "first", now_ms=1_050)
    replacement_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_100, ttl_ms=100
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        fence.execute(side_effects.append, "stale", now_ms=1_101)

    assert isinstance(replacement_token, int)
    assert replacement_token > first_token
    assert side_effects == ["first"]


def test_claim_does_not_delete_an_unrelated_expired_lease(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    assert repository.claim_device_worker_lease(
        "phone-02", "account-02", "worker-2", now_ms=1_000, ttl_ms=100
    )

    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_100, ttl_ms=100
    )

    assert repository.device_worker_lease("phone-02") is not None


class FakeChild:
    def __init__(
        self,
        *,
        pid: int,
        ignores_terminate: bool = False,
        ignored_kill_attempts: int = 0,
    ) -> None:
        self.pid = pid
        self.exitcode: int | None = None
        self.alive = True
        self.ignores_terminate = ignores_terminate
        self.ignored_kill_attempts = ignored_kill_attempts
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = 0

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.ignores_terminate:
            return
        self.alive = False
        self.exitcode = -15

    def join(self, timeout: float | None = None) -> None:
        self.join_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_calls <= self.ignored_kill_attempts:
            return
        self.alive = False
        self.exitcode = -9


def _wait_for_lease_expiry(
    repository: AcquisitionRepository, device_id: str, expires_at_ms: int
) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        lease = repository.device_worker_lease(device_id)
        if lease is not None and int(lease["expires_at_ms"]) >= expires_at_ms:
            return
        time.sleep(0.005)
    raise AssertionError(f"worker lease did not reach {expires_at_ms}")


def _two_device_config(tmp_path: Path) -> FleetConfig:
    return FleetConfig.from_path(
        _write_config(
            tmp_path,
            """
  - device_id: phone-01
    account_id: account-01
    myt_slot: 1
    adb_endpoint: 192.168.28.114:30000
    appium_url: http://127.0.0.1:4723
    order_seed: seed-a
  - device_id: phone-02
    account_id: account-02
    myt_slot: 2
    adb_endpoint: 192.168.28.114:30100
    appium_url: http://127.0.0.1:4723
    order_seed: seed-b
""",
        )
    )


def test_fleet_claims_device_account_lease_before_launch(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    children: dict[str, FakeChild] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        fence.assert_active(now_ms=1_001)
        assert not repository.claim_device_worker_lease(
            device.device_id,
            device.account_id,
            "competing-worker",
            now_ms=1_001,
            ttl_ms=100,
        )
        child = FakeChild(pid=100 + device.myt_slot)
        children[device.device_id] = child
        return child

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    health = supervisor.start()

    assert [item.state for item in health] == [
        FleetWorkerState.HEALTHY,
        FleetWorkerState.HEALTHY,
    ]
    assert set(children) == {"phone-01", "phone-02"}
    supervisor.stop()


def test_fleet_marks_only_exited_child_unhealthy(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    children: dict[str, FakeChild] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        child = FakeChild(pid=100 + device.myt_slot)
        children[device.device_id] = child
        return child

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    supervisor.start()
    children["phone-01"].alive = False
    children["phone-01"].exitcode = 23

    health = {item.device_id: item for item in supervisor.poll(now_ms=1_050)}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "child_exit_23"
    assert health["phone-02"].state is FleetWorkerState.HEALTHY
    assert children["phone-02"].terminate_calls == 0
    assert children["phone-01"].join_calls == 1
    assert repository.fleet_device_health("phone-01")["state"] == "unhealthy"
    assert repository.claim_device_worker_lease(
        "phone-01", "account-01", "replacement", now_ms=1_051, ttl_ms=100
    )
    supervisor.stop()


def test_fleet_does_not_release_lease_for_a_child_that_ignores_terminate(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    children: dict[str, FakeChild] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        child = FakeChild(
            pid=100 + device.myt_slot,
            ignores_terminate=device.device_id == "phone-01",
            ignored_kill_attempts=1 if device.device_id == "phone-01" else 0,
        )
        children[device.device_id] = child
        return child

    now_ms = [1_050]

    def clock_ms() -> int:
        return now_ms[0]

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=clock_ms,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
        heartbeat_interval_seconds=0.01,
    )
    supervisor.start()

    health = {item.device_id: item for item in supervisor.stop()}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "child_termination_timeout"
    assert health["phone-02"].state is FleetWorkerState.STOPPED
    now_ms[0] = 1_140
    _wait_for_lease_expiry(repository, "phone-01", 1_240)
    assert not repository.claim_device_worker_lease(
        "phone-01", "account-01", "replacement", now_ms=1_160, ttl_ms=100
    )
    final_health = {item.device_id: item for item in supervisor.poll(now_ms=1_141)}
    assert final_health["phone-01"].state is FleetWorkerState.STOPPED
    assert children["phone-01"].kill_calls == 2


def test_fleet_renews_lease_while_launcher_is_blocked(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    now_ms = [1_000]
    checked_during_launch = False

    def clock_ms() -> int:
        return now_ms[0]

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        nonlocal checked_during_launch
        if device.device_id == "phone-01":
            now_ms[0] = 1_025
            _wait_for_lease_expiry(repository, "phone-01", 1_055)
            assert not repository.claim_device_worker_lease(
                device.device_id,
                device.account_id,
                "competing-worker",
                now_ms=1_040,
                ttl_ms=30,
            )
            checked_during_launch = True
        return FakeChild(pid=100 + device.myt_slot)

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=clock_ms,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=30,
        heartbeat_interval_seconds=0.005,
    )

    supervisor.start()

    assert checked_during_launch
    supervisor.stop()


def test_lease_contender_does_not_overwrite_current_worker_health(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    config = _two_device_config(tmp_path)
    first = FleetSupervisor(
        repository,
        config,
        launcher=lambda device, owner_id, fence: FakeChild(pid=100 + device.myt_slot),
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"first-{device.device_id}",
        lease_ttl_ms=100,
    )
    second_launches: list[str] = []
    second = FleetSupervisor(
        repository,
        config,
        launcher=lambda device, owner_id, fence: second_launches.append(
            device.device_id
        ),
        clock_ms=lambda: 1_001,
        owner_factory=lambda device: f"second-{device.device_id}",
        lease_ttl_ms=100,
    )
    first.start()

    second.start()

    assert second_launches == []
    stored = repository.fleet_device_health("phone-01")
    assert stored is not None
    assert stored["state"] == "healthy"
    assert stored["owner_id"] == "first-phone-01"
    first.stop()


def test_stale_supervisor_cannot_overwrite_replacement_health(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    config = _two_device_config(tmp_path)
    first_children: dict[str, FakeChild] = {}

    def launch_first(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        child = FakeChild(pid=100 + device.myt_slot)
        first_children[device.device_id] = child
        return child

    first = FleetSupervisor(
        repository,
        config,
        launcher=launch_first,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"first-{device.device_id}",
        lease_ttl_ms=100,
    )
    replacement = FleetSupervisor(
        repository,
        config,
        launcher=lambda device, owner_id, fence: FakeChild(pid=200 + device.myt_slot),
        clock_ms=lambda: 1_100,
        owner_factory=lambda device: f"replacement-{device.device_id}",
        lease_ttl_ms=100,
    )
    first.start()
    replacement.start()

    first.poll(now_ms=1_101)

    stored = repository.fleet_device_health("phone-01")
    assert stored is not None
    assert stored["state"] == "healthy"
    assert stored["owner_id"] == "replacement-phone-01"
    replacement.stop()


def test_guardian_marks_renewal_exception_as_lost(tmp_path: Path, monkeypatch) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    children: dict[str, FakeChild] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        child = FakeChild(pid=100 + device.myt_slot)
        children[device.device_id] = child
        return child

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
        heartbeat_interval_seconds=0.01,
    )
    supervisor.start()
    renewal_attempted = threading.Event()

    def fail_renewal(*args, **kwargs):
        renewal_attempted.set()
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(repository, "renew_device_worker_lease", fail_renewal)
    assert renewal_attempted.wait(timeout=1)

    deadline = time.monotonic() + 1
    while children["phone-01"].is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not children["phone-01"].is_alive()

    deadline = time.monotonic() + 1
    while True:
        health = {item.device_id: item for item in supervisor.poll(now_ms=1_050)}
        if health["phone-01"].state is FleetWorkerState.UNHEALTHY:
            break
        if time.monotonic() >= deadline:
            raise AssertionError("guardian renewal error did not mark worker lost")
        time.sleep(0.005)

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "worker_lease_lost"
    assert children["phone-01"].terminate_calls == 1


def test_fleet_can_restart_after_all_workers_stop(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    launches: list[str] = []

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        launches.append(device.device_id)
        return FakeChild(pid=100 + len(launches))

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    supervisor.start()
    supervisor.stop()
    restarted = supervisor.start()

    assert launches == ["phone-01", "phone-02", "phone-01", "phone-02"]
    assert all(item.state is FleetWorkerState.HEALTHY for item in restarted)
    supervisor.stop()


def test_fleet_isolates_claim_error_to_one_device(tmp_path: Path, monkeypatch) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    original_claim = repository.claim_device_worker_lease
    launches: list[str] = []

    def claim(device_id: str, *args, **kwargs):
        if device_id == "phone-01":
            raise sqlite3.OperationalError("database unavailable")
        return original_claim(device_id, *args, **kwargs)

    monkeypatch.setattr(repository, "claim_device_worker_lease", claim)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=lambda device, owner_id, fence: (
            launches.append(device.device_id) or FakeChild(pid=100 + device.myt_slot)
        ),
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    health = {item.device_id: item for item in supervisor.start()}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "lease_OperationalError"
    assert health["phone-02"].state is FleetWorkerState.HEALTHY
    assert launches == ["phone-02"]
    supervisor.stop()


def test_fleet_restarts_only_requested_missing_device(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    launches: list[str] = []
    children: dict[str, list[FakeChild]] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        launches.append(device.device_id)
        child = FakeChild(pid=100 + len(launches))
        children.setdefault(device.device_id, []).append(child)
        return child

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    supervisor.start()
    original_phone_02 = children["phone-02"][0]
    failed_phone_01 = children["phone-01"][0]
    failed_phone_01.alive = False
    failed_phone_01.exitcode = 1
    supervisor.poll(now_ms=1_010)

    health = {
        item.device_id: item for item in supervisor.restart_devices(("phone-01",))
    }

    assert launches == ["phone-01", "phone-02", "phone-01"]
    assert original_phone_02.is_alive() is True
    assert health["phone-01"].state is FleetWorkerState.HEALTHY
    assert health["phone-02"].state is FleetWorkerState.HEALTHY
    supervisor.stop()


def test_launcher_and_health_write_errors_are_isolated_to_one_device(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    original_record = repository.record_fleet_device_health
    launches: list[str] = []

    def record(device_id: str, account_id: str, state: str, **kwargs):
        if device_id == "phone-01" and state == "unhealthy":
            raise sqlite3.OperationalError("database unavailable")
        return original_record(device_id, account_id, state, **kwargs)

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        launches.append(device.device_id)
        if device.device_id == "phone-01":
            raise RuntimeError("launcher failed")
        return FakeChild(pid=100 + device.myt_slot)

    monkeypatch.setattr(repository, "record_fleet_device_health", record)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    health = {item.device_id: item for item in supervisor.start()}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "launch_RuntimeError"
    assert health["phone-02"].state is FleetWorkerState.HEALTHY
    assert launches == ["phone-01", "phone-02"]
    supervisor.stop()


def test_launcher_lost_guard_keeps_stubborn_child_tracked(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    renewal_attempted = threading.Event()
    children: dict[str, FakeChild] = {}

    def fail_renewal(*args, **kwargs):
        renewal_attempted.set()
        raise sqlite3.OperationalError("database unavailable")

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        assert renewal_attempted.wait(timeout=1)
        child = FakeChild(
            pid=100 + device.myt_slot,
            ignores_terminate=True,
            ignored_kill_attempts=1,
        )
        children[device.device_id] = child
        return child

    monkeypatch.setattr(repository, "renew_device_worker_lease", fail_renewal)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
        heartbeat_interval_seconds=0.005,
    )

    started = {item.device_id: item for item in supervisor.start()}
    final = {item.device_id: item for item in supervisor.poll(now_ms=1_010)}

    assert started["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert final["phone-01"].state is FleetWorkerState.STOPPED
    assert children["phone-01"].kill_calls == 2


def test_health_write_error_isolated_and_stubborn_child_remains_tracked(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    original_record = repository.record_fleet_device_health
    children: dict[str, FakeChild] = {}

    def record(device_id: str, account_id: str, state: str, **kwargs):
        if device_id == "phone-01" and state != "starting":
            raise sqlite3.OperationalError("database unavailable")
        return original_record(device_id, account_id, state, **kwargs)

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        child = FakeChild(
            pid=100 + device.myt_slot,
            ignores_terminate=device.device_id == "phone-01",
            ignored_kill_attempts=1 if device.device_id == "phone-01" else 0,
        )
        children[device.device_id] = child
        return child

    monkeypatch.setattr(repository, "record_fleet_device_health", record)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    health = {item.device_id: item for item in supervisor.start()}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-02"].state is FleetWorkerState.HEALTHY
    assert set(children) == {"phone-01", "phone-02"}
    final = {item.device_id: item for item in supervisor.poll(now_ms=1_010)}
    assert final["phone-01"].state is FleetWorkerState.STOPPED
    supervisor.stop()


def test_stop_cancels_a_blocked_launcher(tmp_path: Path) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    launcher_entered = threading.Event()
    release_launcher = threading.Event()
    children: dict[str, FakeChild] = {}

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        launcher_entered.set()
        assert release_launcher.wait(timeout=1)
        child = FakeChild(pid=100 + device.myt_slot)
        children[device.device_id] = child
        return child

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    start_thread = threading.Thread(target=supervisor.start)
    start_thread.start()
    assert launcher_entered.wait(timeout=1)

    supervisor.stop()
    release_launcher.set()
    start_thread.join(timeout=1)

    assert not start_thread.is_alive()
    assert children["phone-01"].is_alive() is False
    assert repository.device_worker_lease("phone-01") is None


def test_stop_cancels_between_alive_check_and_worker_registration(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    alive_check_entered = threading.Event()
    release_alive_check = threading.Event()

    class BlockingAliveChild(FakeChild):
        def __init__(self, *, pid: int) -> None:
            super().__init__(pid=pid)
            self.blocked_once = False

        def is_alive(self) -> bool:
            if not self.blocked_once:
                self.blocked_once = True
                alive_check_entered.set()
                assert release_alive_check.wait(timeout=1)
            return super().is_alive()

    child = BlockingAliveChild(pid=101)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=lambda device, owner_id, fence: child,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    start_thread = threading.Thread(target=supervisor.start)
    start_thread.start()
    assert alive_check_entered.wait(timeout=1)

    supervisor.stop()
    release_alive_check.set()
    start_thread.join(timeout=1)

    assert not start_thread.is_alive()
    assert child.is_alive() is False
    assert repository.device_worker_lease("phone-01") is None


def test_stop_cannot_be_overwritten_by_late_healthy_publication(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    healthy_write_entered = threading.Event()
    release_healthy_write = threading.Event()
    child = FakeChild(
        pid=101,
        ignores_terminate=True,
        ignored_kill_attempts=1,
    )
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=lambda device, owner_id, fence: child,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    original_record = supervisor._record

    def block_healthy_write(device, state, **kwargs):
        if state is FleetWorkerState.HEALTHY:
            healthy_write_entered.set()
            assert release_healthy_write.wait(timeout=1)
        return original_record(device, state, **kwargs)

    monkeypatch.setattr(supervisor, "_record", block_healthy_write)
    start_thread = threading.Thread(target=supervisor.start)
    start_thread.start()
    assert healthy_write_entered.wait(timeout=1)

    stopped = {item.device_id: item for item in supervisor.stop()}
    assert stopped["phone-01"].state is FleetWorkerState.UNHEALTHY
    release_healthy_write.set()
    start_thread.join(timeout=1)

    assert not start_thread.is_alive()
    final = {item.device_id: item for item in supervisor.health()}
    assert final["phone-01"].state is FleetWorkerState.UNHEALTHY


def test_stop_preserves_completed_state_during_late_healthy_publication(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    healthy_write_entered = threading.Event()
    release_healthy_write = threading.Event()
    errors: list[Exception] = []
    child = FakeChild(pid=101)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=lambda device, owner_id, fence: child,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    original_record = supervisor._record

    def block_healthy_write(device, state, **kwargs):
        if state is FleetWorkerState.HEALTHY:
            healthy_write_entered.set()
            assert release_healthy_write.wait(timeout=1)
        return original_record(device, state, **kwargs)

    def start() -> None:
        try:
            supervisor.start()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(supervisor, "_record", block_healthy_write)
    start_thread = threading.Thread(target=start)
    start_thread.start()
    assert healthy_write_entered.wait(timeout=1)

    stopped = {item.device_id: item for item in supervisor.stop()}
    assert stopped["phone-01"].state is FleetWorkerState.STOPPED
    release_healthy_write.set()
    start_thread.join(timeout=1)

    assert not start_thread.is_alive()
    assert errors == []
    final = {item.device_id: item for item in supervisor.health()}
    assert final["phone-01"].state is FleetWorkerState.STOPPED


def test_stop_cancels_guardian_lost_registration_window(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    renewal_attempted = threading.Event()
    alive_check_entered = threading.Event()
    release_alive_check = threading.Event()

    class LostBlockingChild(FakeChild):
        def __init__(self, *, pid: int) -> None:
            super().__init__(
                pid=pid,
                ignores_terminate=True,
                ignored_kill_attempts=1,
            )
            self.blocked_once = False

        def is_alive(self) -> bool:
            if not self.blocked_once:
                self.blocked_once = True
                alive_check_entered.set()
                assert release_alive_check.wait(timeout=1)
            return super().is_alive()

    child = LostBlockingChild(pid=101)

    def fail_renewal(*args, **kwargs):
        renewal_attempted.set()
        raise sqlite3.OperationalError("database unavailable")

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        assert renewal_attempted.wait(timeout=1)
        return child

    monkeypatch.setattr(repository, "renew_device_worker_lease", fail_renewal)
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
        heartbeat_interval_seconds=0.005,
    )
    start_thread = threading.Thread(target=supervisor.start)
    start_thread.start()
    assert alive_check_entered.wait(timeout=1)

    supervisor.stop()
    release_alive_check.set()
    start_thread.join(timeout=1)

    assert not start_thread.is_alive()
    assert child.is_alive() is False


def test_cancel_release_error_still_allows_restart(tmp_path: Path, monkeypatch) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    now_ms = [1_000]
    launcher_entered = threading.Event()
    release_launcher = threading.Event()
    original_release = repository.release_device_worker_lease
    errors: list[Exception] = []

    def launch(device, owner_id: str, fence: DeviceWorkerFence) -> FakeChild:
        launcher_entered.set()
        assert release_launcher.wait(timeout=1)
        return FakeChild(pid=100 + device.myt_slot)

    def fail_release(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=launch,
        clock_ms=lambda: now_ms[0],
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )

    def start() -> None:
        try:
            supervisor.start()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(repository, "release_device_worker_lease", fail_release)
    start_thread = threading.Thread(target=start)
    start_thread.start()
    assert launcher_entered.wait(timeout=1)
    supervisor.stop()
    release_launcher.set()
    start_thread.join(timeout=1)

    assert errors == []
    monkeypatch.setattr(repository, "release_device_worker_lease", original_release)
    now_ms[0] = 1_100
    restarted = supervisor.start()
    assert any(item.state is FleetWorkerState.HEALTHY for item in restarted)
    supervisor.stop()


def test_stop_release_error_records_unhealthy_without_raising(
    tmp_path: Path, monkeypatch
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db")
    repository.migrate()
    supervisor = FleetSupervisor(
        repository,
        _two_device_config(tmp_path),
        launcher=lambda device, owner_id, fence: FakeChild(pid=100 + device.myt_slot),
        clock_ms=lambda: 1_000,
        owner_factory=lambda device: f"worker-{device.device_id}",
        lease_ttl_ms=100,
    )
    supervisor.start()

    def fail_release(*args, **kwargs):
        raise sqlite3.OperationalError("database unavailable")

    monkeypatch.setattr(repository, "release_device_worker_lease", fail_release)
    health = {item.device_id: item for item in supervisor.stop()}

    assert health["phone-01"].state is FleetWorkerState.UNHEALTHY
    assert health["phone-01"].error_code == "worker_lease_release_failed"

import ipaddress
import os
import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

import yaml

from .acquisition_db import AcquisitionRepository


def _configured_port(value: object, default: int, label: str) -> int:
    raw_value = default if value is None else value
    try:
        port = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an integer from 1 to 65535") from error
    if not 1 <= port <= 65_535:
        raise ValueError(f"{label} must be from 1 to 65535")
    return port


def _configured_ip(value: object, label: str, *, loopback: bool = False) -> str:
    normalized = str(value or "").strip()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as error:
        raise ValueError(f"{label} must be an IP address") from error
    if address.is_unspecified:
        raise ValueError(f"{label} must be a specific address")
    if loopback and not address.is_loopback:
        raise ValueError(f"{label} must be a loopback address")
    return str(address)


def _validate_adb_endpoint(value: str) -> None:
    try:
        parsed = urlsplit(f"tcp://{value}")
        host = parsed.hostname
        port = parsed.port
        if host is None or port is None or not 1 <= port <= 65_535:
            raise ValueError
        ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError("ADB endpoint must be an IP address and port") from error


def _validate_appium_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Appium URL is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or port is None
    ):
        raise ValueError("Appium URL must include HTTP scheme, host, and port")


@dataclass(frozen=True)
class FleetDevice:
    device_id: str
    account_id: str
    myt_slot: int
    adb_endpoint: str
    appium_url: str
    order_seed: str


@dataclass(frozen=True)
class FleetConfig:
    myt_host: str
    myt_sdk_port: int
    relay_bind_host: str
    relay_bind_port: int
    relay_upstream_host: str
    relay_upstream_port: int
    relay_allowed_sources: frozenset[str]
    devices: tuple[FleetDevice, ...]

    @classmethod
    def from_path(cls, path: Path) -> "FleetConfig":
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError("fleet configuration must be a mapping")
        myt = payload.get("myt") or {}
        relay = payload.get("proxy_relay") or {}
        raw_devices = payload.get("devices") or []
        if (
            not isinstance(myt, dict)
            or not isinstance(relay, dict)
            or not isinstance(raw_devices, list)
        ):
            raise ValueError("fleet configuration sections are invalid")
        myt_host = _configured_ip(myt.get("host"), "MYT host")
        myt_sdk_port = _configured_port(myt.get("sdk_port"), 8000, "MYT SDK port")
        relay_bind_host = _configured_ip(relay.get("bind_host"), "relay bind host")
        relay_upstream_host = _configured_ip(
            relay.get("upstream_host", "127.0.0.1"),
            "relay upstream host",
            loopback=True,
        )
        relay_bind_port = _configured_port(
            relay.get("bind_port"), 7898, "relay bind port"
        )
        relay_upstream_port = _configured_port(
            relay.get("upstream_port"), 7897, "relay upstream port"
        )
        if any(not isinstance(item, dict) for item in raw_devices):
            raise ValueError("device entry must be a mapping")
        devices = tuple(
            FleetDevice(
                device_id=str(item.get("device_id") or "").strip(),
                account_id=str(item.get("account_id") or "").strip(),
                myt_slot=int(item.get("myt_slot") or 0),
                adb_endpoint=str(item.get("adb_endpoint") or "").strip(),
                appium_url=str(item.get("appium_url") or "").strip(),
                order_seed=str(item.get("order_seed") or "").strip(),
            )
            for item in raw_devices
        )
        if not devices:
            raise ValueError("fleet must contain at least one device")
        if any(device.myt_slot <= 0 for device in devices):
            raise ValueError("MYT slot must be positive")
        for device in devices:
            _validate_adb_endpoint(device.adb_endpoint)
            _validate_appium_url(device.appium_url)
        fields = {
            "device id": [device.device_id for device in devices],
            "account id": [device.account_id for device in devices],
            "MYT slot": [device.myt_slot for device in devices],
            "ADB endpoint": [device.adb_endpoint for device in devices],
            "order seed": [device.order_seed for device in devices],
        }
        for label, values in fields.items():
            if any(value in ("", 0) for value in values):
                raise ValueError(f"{label} is required")
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate {label}")
        if any(not device.appium_url for device in devices):
            raise ValueError("Appium URL is required")
        return cls(
            myt_host=myt_host,
            myt_sdk_port=myt_sdk_port,
            relay_bind_host=relay_bind_host,
            relay_bind_port=relay_bind_port,
            relay_upstream_host=relay_upstream_host,
            relay_upstream_port=relay_upstream_port,
            relay_allowed_sources=frozenset({myt_host}),
            devices=devices,
        )


class DeviceWorkerLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceWorkerFence:
    database_path: Path
    device_id: str
    account_id: str
    owner_id: str
    fence_token: int

    def assert_active(self, *, now_ms: int | None = None) -> None:
        observed_at_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        repository = AcquisitionRepository(self.database_path)
        if not repository.device_worker_fence_is_active(
            self.device_id,
            self.account_id,
            self.owner_id,
            self.fence_token,
            now_ms=observed_at_ms,
        ):
            raise DeviceWorkerLeaseLost(
                f"device worker fence is inactive for {self.device_id}"
            )

    def execute(self, operation, *args, now_ms: int | None = None, **kwargs):
        self.assert_active(now_ms=now_ms)
        return operation(*args, **kwargs)


class FleetWorkerState(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


@dataclass(frozen=True)
class FleetWorkerHealth:
    device_id: str
    account_id: str
    state: FleetWorkerState
    owner_id: str | None
    fence_token: int | None
    process_id: int | None
    error_code: str | None
    updated_at_ms: int


class WorkerProcess(Protocol):
    pid: int | None
    exitcode: int | None

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


@dataclass(frozen=True)
class _RunningWorker:
    device: FleetDevice
    owner_id: str
    process: WorkerProcess
    lease_guard: "_WorkerLeaseGuard"


class _WorkerLeaseGuard:
    def __init__(
        self,
        repository: AcquisitionRepository,
        device: FleetDevice,
        owner_id: str,
        fence_token: int,
        *,
        clock_ms: Callable[[], int],
        ttl_ms: int,
        interval_seconds: float,
        join_timeout: float,
    ) -> None:
        self.repository = repository
        self.device = device
        self.owner_id = owner_id
        self.fence_token = fence_token
        self.clock_ms = clock_ms
        self.ttl_ms = ttl_ms
        self.interval_seconds = interval_seconds
        self.join_timeout = join_timeout
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._process_lock = threading.Lock()
        self._process: WorkerProcess | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"fleet-lease-{device.device_id}",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def attach(self, process: WorkerProcess) -> None:
        with self._process_lock:
            self._process = process
            lost = self._lost.is_set()
        if lost:
            _terminate_process(process, self.join_timeout)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds * 2))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.repository.renew_device_worker_lease(
                    self.device.device_id,
                    self.device.account_id,
                    self.owner_id,
                    now_ms=self.clock_ms(),
                    ttl_ms=self.ttl_ms,
                    fence_token=self.fence_token,
                )
            except Exception:
                self._lost.set()
                with self._process_lock:
                    process = self._process
                if process is not None:
                    _terminate_process(process, self.join_timeout)
                return


def _terminate_process(process: WorkerProcess, join_timeout: float) -> bool:
    if process.is_alive():
        try:
            process.terminate()
        except Exception:
            pass
        process.join(timeout=join_timeout)
    if process.is_alive():
        try:
            process.kill()
        except Exception:
            pass
        process.join(timeout=join_timeout)
    return not process.is_alive()


def _default_owner_id(device: FleetDevice) -> str:
    return f"fleet-{os.getpid()}-{device.device_id}-{uuid.uuid4().hex}"


class FleetSupervisor:
    def __init__(
        self,
        repository: AcquisitionRepository,
        config: FleetConfig,
        *,
        launcher: Callable[[FleetDevice, str, DeviceWorkerFence], WorkerProcess],
        clock_ms: Callable[[], int],
        owner_factory: Callable[[FleetDevice], str] = _default_owner_id,
        lease_ttl_ms: int = 120_000,
        join_timeout: float = 2.0,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        if lease_ttl_ms <= 0:
            raise ValueError("fleet worker lease TTL must be positive")
        self.repository = repository
        self.config = config
        self.launcher = launcher
        self.clock_ms = clock_ms
        self.owner_factory = owner_factory
        self.lease_ttl_ms = lease_ttl_ms
        self.join_timeout = join_timeout
        default_interval = min(30.0, max(0.01, lease_ttl_ms / 3_000))
        self.heartbeat_interval_seconds = (
            default_interval
            if heartbeat_interval_seconds is None
            else float(heartbeat_interval_seconds)
        )
        if not 0 < self.heartbeat_interval_seconds * 1_000 < lease_ttl_ms:
            raise ValueError("fleet heartbeat interval must be shorter than lease TTL")
        self._started = False
        self._starting = False
        self._workers: dict[str, _RunningWorker] = {}
        self._health: dict[str, FleetWorkerHealth] = {}
        self._stopping: set[str] = set()
        self._state_lock = threading.RLock()
        self._cancel_start = threading.Event()

    def start(self) -> tuple[FleetWorkerHealth, ...]:
        with self._state_lock:
            if self._started or self._starting:
                return self.health()
            self._started = True
            self._starting = True
            self._cancel_start.clear()
        try:
            self._start_devices()
        finally:
            with self._state_lock:
                self._starting = False
                if not self._workers:
                    self._started = False
        return self.health()

    def _start_devices(self) -> None:
        for device in self.config.devices:
            if self._cancel_start.is_set():
                break
            try:
                now_ms = self.clock_ms()
                owner_id = str(self.owner_factory(device)).strip()
                if not owner_id:
                    raise ValueError("fleet worker owner identifier is required")
                claimed = self.repository.claim_device_worker_lease(
                    device.device_id,
                    device.account_id,
                    owner_id,
                    now_ms=now_ms,
                    ttl_ms=self.lease_ttl_ms,
                )
            except Exception as error:
                self._record(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=self.clock_ms(),
                    error_code=f"lease_{type(error).__name__}",
                    persist=False,
                )
                continue
            if not claimed:
                self._record(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=now_ms,
                    error_code="worker_lease_unavailable",
                    persist=False,
                )
                continue
            fence_token = int(claimed)
            worker_fence = DeviceWorkerFence(
                database_path=self.repository.path,
                device_id=device.device_id,
                account_id=device.account_id,
                owner_id=owner_id,
                fence_token=fence_token,
            )
            lease_guard = _WorkerLeaseGuard(
                self.repository,
                device,
                owner_id,
                fence_token,
                clock_ms=self.clock_ms,
                ttl_ms=self.lease_ttl_ms,
                interval_seconds=self.heartbeat_interval_seconds,
                join_timeout=self.join_timeout,
            )
            lease_guard.start()
            try:
                starting_recorded = self._record(
                    device,
                    FleetWorkerState.STARTING,
                    now_ms=now_ms,
                    owner_id=owner_id,
                    fence_token=fence_token,
                    require_active_lease=True,
                )
            except Exception as error:
                lease_guard.stop()
                self._release(device, owner_id, fence_token)
                self._record_with_fallback(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=self.clock_ms(),
                    error_code=f"health_{type(error).__name__}",
                    persist=False,
                )
                continue
            if not starting_recorded:
                lease_guard.stop()
                self._release(device, owner_id, fence_token)
                self._record(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=self.clock_ms(),
                    error_code="worker_lease_lost",
                    persist=False,
                )
                continue
            try:
                process = self.launcher(device, owner_id, worker_fence)
            except Exception as error:
                lease_guard.stop()
                self._release(device, owner_id, fence_token)
                self._record_with_fallback(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=self.clock_ms(),
                    owner_id=owner_id,
                    fence_token=fence_token,
                    error_code=f"launch_{type(error).__name__}",
                    expected_owner_id=owner_id,
                    expected_fence_token=fence_token,
                )
                continue
            if self._cancel_start.is_set():
                self._cancel_launched_process(
                    device, owner_id, fence_token, lease_guard, process
                )
                break
            lease_guard.attach(process)
            if lease_guard.lost:
                process.join(timeout=self.join_timeout)
                if process.is_alive():
                    with self._state_lock:
                        cancelled = self._cancel_start.is_set()
                        if not cancelled:
                            self._workers[device.device_id] = _RunningWorker(
                                device=device,
                                owner_id=owner_id,
                                process=process,
                                lease_guard=lease_guard,
                            )
                            self._stopping.add(device.device_id)
                    if cancelled:
                        self._cancel_launched_process(
                            device, owner_id, fence_token, lease_guard, process
                        )
                        break
                    self._record_with_fallback(
                        device,
                        FleetWorkerState.UNHEALTHY,
                        now_ms=self.clock_ms(),
                        owner_id=owner_id,
                        fence_token=fence_token,
                        process_id=process.pid,
                        error_code="child_termination_timeout",
                        expected_owner_id=owner_id,
                        expected_fence_token=fence_token,
                    )
                    continue
                lease_guard.stop()
                self._release(device, owner_id, fence_token)
                self._record_with_fallback(
                    device,
                    FleetWorkerState.UNHEALTHY,
                    now_ms=self.clock_ms(),
                    owner_id=owner_id,
                    fence_token=fence_token,
                    process_id=process.pid,
                    error_code="worker_lease_lost",
                    expected_owner_id=owner_id,
                    expected_fence_token=fence_token,
                )
                continue
            if not process.is_alive():
                process.join(timeout=self.join_timeout)
                lease_guard.stop()
                self._release(device, owner_id, fence_token)
                self._record_child_exit(
                    device,
                    owner_id,
                    process,
                    now_ms=self.clock_ms(),
                    fence_token=fence_token,
                )
                continue
            running = _RunningWorker(
                device=device,
                owner_id=owner_id,
                process=process,
                lease_guard=lease_guard,
            )
            with self._state_lock:
                cancelled = self._cancel_start.is_set()
                if not cancelled:
                    self._workers[device.device_id] = running
            if cancelled:
                self._cancel_launched_process(
                    device, owner_id, fence_token, lease_guard, process
                )
                break
            health_write_failed = False
            try:
                healthy_recorded = self._record(
                    device,
                    FleetWorkerState.HEALTHY,
                    now_ms=self.clock_ms(),
                    owner_id=owner_id,
                    process_id=process.pid,
                    require_active_lease=True,
                    expected_running_worker=running,
                )
            except Exception:
                healthy_recorded = False
                health_write_failed = True
            if not healthy_recorded or lease_guard.lost:
                with self._state_lock:
                    if (
                        self._cancel_start.is_set()
                        or self._workers.get(device.device_id) is not running
                        or device.device_id in self._stopping
                    ):
                        break
                    terminated = _terminate_process(process, self.join_timeout)
                    try:
                        self._record(
                            device,
                            FleetWorkerState.UNHEALTHY,
                            now_ms=self.clock_ms(),
                            owner_id=owner_id,
                            process_id=process.pid,
                            error_code="worker_lease_lost",
                            expected_owner_id=owner_id,
                            persist=not health_write_failed,
                        )
                    except Exception:
                        self._record(
                            device,
                            FleetWorkerState.UNHEALTHY,
                            now_ms=self.clock_ms(),
                            owner_id=owner_id,
                            process_id=process.pid,
                            error_code="worker_lease_lost",
                            persist=False,
                        )
                    if terminated:
                        lease_guard.stop()
                        self._release(device, owner_id, fence_token)
                        if self._workers.get(device.device_id) is running:
                            del self._workers[device.device_id]
                    else:
                        self._stopping.add(device.device_id)

    def _cancel_launched_process(
        self,
        device: FleetDevice,
        owner_id: str,
        fence_token: int,
        lease_guard: _WorkerLeaseGuard,
        process: WorkerProcess,
    ) -> None:
        terminated = _terminate_process(process, self.join_timeout)
        if terminated:
            lease_guard.stop()
        released = self._release(device, owner_id, fence_token) if terminated else False
        if not terminated:
            with self._state_lock:
                self._workers[device.device_id] = _RunningWorker(
                    device=device,
                    owner_id=owner_id,
                    process=process,
                    lease_guard=lease_guard,
                )
                self._stopping.add(device.device_id)
        state = (
            FleetWorkerState.STOPPED
            if terminated and released
            else FleetWorkerState.UNHEALTHY
        )
        error_code = None
        if not terminated:
            error_code = "child_termination_timeout"
        elif not released:
            error_code = "worker_lease_release_failed"
        try:
            self._record(
                device,
                state,
                now_ms=self.clock_ms(),
                owner_id=owner_id,
                fence_token=fence_token,
                process_id=process.pid,
                error_code=error_code,
                expected_owner_id=owner_id,
                expected_fence_token=fence_token,
            )
        except Exception:
            self._record(
                device,
                state,
                now_ms=self.clock_ms(),
                owner_id=owner_id,
                fence_token=fence_token,
                process_id=process.pid,
                error_code=error_code,
                persist=False,
            )

    def poll(self, *, now_ms: int | None = None) -> tuple[FleetWorkerHealth, ...]:
        with self._state_lock:
            observed_at_ms = self.clock_ms() if now_ms is None else now_ms
            for device_id, running in tuple(self._workers.items()):
                if device_id in self._stopping:
                    if _terminate_process(running.process, self.join_timeout):
                        self._finish_stopped(running, now_ms=observed_at_ms)
                    else:
                        self._record(
                            running.device,
                            FleetWorkerState.UNHEALTHY,
                            now_ms=observed_at_ms,
                            owner_id=running.owner_id,
                            process_id=running.process.pid,
                            error_code="child_termination_timeout",
                            require_active_lease=True,
                        )
                    continue
                if running.lease_guard.lost:
                    _terminate_process(running.process, self.join_timeout)
                    if running.process.is_alive():
                        self._record(
                            running.device,
                            FleetWorkerState.UNHEALTHY,
                            now_ms=observed_at_ms,
                            owner_id=running.owner_id,
                            process_id=running.process.pid,
                            error_code="child_termination_timeout",
                            expected_owner_id=running.owner_id,
                        )
                        continue
                    running.lease_guard.stop()
                    self._release(
                        running.device,
                        running.owner_id,
                        running.lease_guard.fence_token,
                    )
                    self._record(
                        running.device,
                        FleetWorkerState.UNHEALTHY,
                        now_ms=observed_at_ms,
                        owner_id=running.owner_id,
                        process_id=running.process.pid,
                        error_code="worker_lease_lost",
                        expected_owner_id=running.owner_id,
                    )
                    del self._workers[device_id]
                    continue
                if not running.process.is_alive():
                    running.process.join(timeout=self.join_timeout)
                    running.lease_guard.stop()
                    self._release(
                        running.device,
                        running.owner_id,
                        running.lease_guard.fence_token,
                    )
                    self._record_child_exit(
                        running.device,
                        running.owner_id,
                        running.process,
                        now_ms=observed_at_ms,
                    )
                    del self._workers[device_id]
                    continue
            if not self._workers and self._stopping:
                self._stopping.clear()
                self._started = False
            return self.health()

    def stop(self) -> tuple[FleetWorkerHealth, ...]:
        self._cancel_start.set()
        with self._state_lock:
            now_ms = self.clock_ms()
            for device_id, running in tuple(self._workers.items()):
                self._stopping.add(device_id)
                if not _terminate_process(running.process, self.join_timeout):
                    self._record(
                        running.device,
                        FleetWorkerState.UNHEALTHY,
                        now_ms=now_ms,
                        owner_id=running.owner_id,
                        process_id=running.process.pid,
                        error_code="child_termination_timeout",
                        require_active_lease=True,
                    )
                    continue
                self._finish_stopped(running, now_ms=now_ms)
            if not self._workers and not self._starting:
                self._stopping.clear()
                self._started = False
            return self.health()

    def _finish_stopped(self, running: _RunningWorker, *, now_ms: int) -> None:
        device_id = running.device.device_id
        running.lease_guard.stop()
        released = self._release(
            running.device,
            running.owner_id,
            running.lease_guard.fence_token,
        )
        state = FleetWorkerState.STOPPED if released else FleetWorkerState.UNHEALTHY
        error_code = None if released else "worker_lease_release_failed"
        try:
            self._record(
                running.device,
                state,
                now_ms=now_ms,
                owner_id=running.owner_id,
                process_id=running.process.pid,
                error_code=error_code,
                expected_owner_id=running.owner_id,
            )
        except Exception:
            self._record(
                running.device,
                state,
                now_ms=now_ms,
                owner_id=running.owner_id,
                process_id=running.process.pid,
                error_code=error_code,
                persist=False,
            )
        del self._workers[device_id]
        self._stopping.discard(device_id)

    def health(self) -> tuple[FleetWorkerHealth, ...]:
        with self._state_lock:
            return tuple(
                self._health[device.device_id]
                for device in self.config.devices
                if device.device_id in self._health
            )

    def _release(self, device: FleetDevice, owner_id: str, fence_token: int) -> bool:
        try:
            self.repository.release_device_worker_lease(
                device.device_id,
                device.account_id,
                owner_id,
                fence_token=fence_token,
            )
        except Exception:
            return False
        return True

    def _record_child_exit(
        self,
        device: FleetDevice,
        owner_id: str,
        process: WorkerProcess,
        *,
        now_ms: int,
        fence_token: int | None = None,
    ) -> None:
        exit_code = process.exitcode
        error_code = (
            "child_exit_unknown" if exit_code is None else f"child_exit_{exit_code}"
        )
        self._record_with_fallback(
            device,
            FleetWorkerState.UNHEALTHY,
            now_ms=now_ms,
            owner_id=owner_id,
            fence_token=fence_token,
            process_id=process.pid,
            error_code=error_code,
            expected_owner_id=owner_id,
            expected_fence_token=fence_token,
        )

    def _record_with_fallback(
        self, device: FleetDevice, state: FleetWorkerState, **kwargs
    ) -> bool:
        try:
            return self._record(device, state, **kwargs)
        except Exception:
            local_kwargs = dict(kwargs)
            local_kwargs["persist"] = False
            return self._record(device, state, **local_kwargs)

    def _record(
        self,
        device: FleetDevice,
        state: FleetWorkerState,
        *,
        now_ms: int,
        owner_id: str | None = None,
        fence_token: int | None = None,
        process_id: int | None = None,
        error_code: str | None = None,
        expected_owner_id: str | None = None,
        expected_fence_token: int | None = None,
        require_active_lease: bool = False,
        expected_running_worker: _RunningWorker | None = None,
        persist: bool = True,
    ) -> bool:
        if (
            fence_token is None
            and expected_running_worker is not None
            and expected_running_worker.owner_id == owner_id
        ):
            fence_token = expected_running_worker.lease_guard.fence_token
        elif fence_token is None and owner_id is not None:
            running = self._workers.get(device.device_id)
            if running is not None and running.owner_id == owner_id:
                fence_token = running.lease_guard.fence_token
        if expected_owner_id is not None and expected_fence_token is None:
            expected_fence_token = fence_token
        health = FleetWorkerHealth(
            device_id=device.device_id,
            account_id=device.account_id,
            state=state,
            owner_id=owner_id,
            fence_token=fence_token,
            process_id=process_id,
            error_code=error_code,
            updated_at_ms=now_ms,
        )

        def persist_health() -> bool:
            if not persist:
                return True
            if fence_token is None:
                raise ValueError("persisted fleet health requires a fence token")
            return self.repository.record_fleet_device_health(
                device.device_id,
                device.account_id,
                state.value,
                now_ms=now_ms,
                owner_id=owner_id,
                fence_token=fence_token,
                process_id=process_id,
                error_code=error_code,
                expected_owner_id=expected_owner_id,
                expected_fence_token=expected_fence_token,
                require_active_lease=require_active_lease,
            )

        if expected_running_worker is not None:
            with self._state_lock:
                if (
                    self._cancel_start.is_set()
                    or device.device_id in self._stopping
                    or self._workers.get(device.device_id)
                    is not expected_running_worker
                ):
                    return False
                self._health[device.device_id] = health
                return persist_health()
        with self._state_lock:
            self._health[device.device_id] = health
        return persist_health()

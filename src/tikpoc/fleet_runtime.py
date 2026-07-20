import multiprocessing
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .acquisition_db import AcquisitionRepository
from .acquisition_models import AssignmentPhase, OutcomeKind, PoolTarget
from .device import AppiumTikTokDevice
from .fleet import (
    DeviceWorkerFence,
    FleetConfig,
    FleetDevice,
    FleetSupervisor,
    FleetWorkerState,
)
from .mobile_worker import MobileAssignmentWorker
from .mobile_routes import AdbProfileRouter
from .proxy_relay import ProxyRelay
from .runner import create_driver


def _clock_ms() -> int:
    return int(time.time() * 1_000)


class StopEvent(Protocol):
    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class FencedVerifiedDevice:
    def __init__(self, device: object, fence: DeviceWorkerFence) -> None:
        self.device = device
        self.fence = fence

    def ensure_ready(self) -> None:
        self.fence.execute(self.device.ensure_ready)

    def open_target(self, target: PoolTarget) -> None:
        self.fence.execute(self.device.open_target, target)

    def confirm_profile_identity(self, target: PoolTarget) -> None:
        self.fence.execute(self.device.confirm_profile_identity, target)

    def read_profile_observation(self):
        return self.fence.execute(self.device.read_profile_observation)

    def list_video_keys(self) -> tuple[str, ...]:
        return self.fence.execute(self.device.list_video_keys)

    def open_and_confirm_video(self, video_key: str) -> None:
        self.fence.execute(self.device.open_and_confirm_video, video_key)

    def execute_outcome(self, outcome: OutcomeKind):
        return self.fence.execute(self.device.execute_outcome, outcome)

    def reconcile_outcome(self, outcome: OutcomeKind):
        return self.fence.execute(self.device.reconcile_outcome, outcome)

    def capture_diagnostics(self):
        return self.fence.execute(self.device.capture_diagnostics)

    def recover(self, phase: AssignmentPhase) -> None:
        self.fence.execute(self.device.recover, phase)


def run_device_worker(
    database_path: Path,
    round_id: str,
    device: FleetDevice,
    fence: DeviceWorkerFence,
    stop_event: StopEvent,
    *,
    idle_sleep_seconds: float = 0.5,
    repository_factory: Callable[[Path], object] = AcquisitionRepository,
    driver_factory: Callable[[str, str], object] = create_driver,
    device_factory: Callable[[object], object] = AppiumTikTokDevice,
    worker_factory: Callable[..., object] = MobileAssignmentWorker,
    route_factory: Callable[[str], object] = AdbProfileRouter,
    clock_ms: Callable[[], int] = _clock_ms,
) -> None:
    repository = repository_factory(database_path)
    driver = fence.execute(driver_factory, device.appium_url, device.adb_endpoint)
    try:
        raw_device = device_factory(driver)
        raw_device.route_opener = route_factory(device.adb_endpoint).open
        verified_device = FencedVerifiedDevice(raw_device, fence)
        worker = worker_factory(
            repository,
            verified_device,
            device_id=device.device_id,
            owner_id=fence.owner_id,
            worker_account_id=fence.account_id,
            worker_fence_token=fence.fence_token,
            clock_ms=clock_ms,
        )
        while not stop_event.is_set():
            fence.assert_active()
            assignment = repository.claim_next_assignment(
                round_id,
                device.device_id,
                fence.owner_id,
                now_ms=clock_ms(),
            )
            if assignment is None:
                stop_event.wait(idle_sleep_seconds)
                continue
            worker.run_assignment(assignment)
    finally:
        driver.quit()


def _process_launcher(
    context,
    database_path: Path,
    round_id: str,
    stop_event: StopEvent,
):
    def launch(device: FleetDevice, owner_id: str, fence: DeviceWorkerFence) -> object:
        process = context.Process(
            target=run_device_worker,
            args=(database_path, round_id, device, fence, stop_event),
            name=f"tikpoc-{device.device_id}",
        )
        process.start()
        return process

    return launch


def _round_completed(completion) -> bool:
    return (
        completion.total > 0
        and completion.completed + completion.skipped == completion.total
    )


def _active_device_ids(health) -> set[str]:
    return {
        item.device_id
        for item in health
        if item.state in {FleetWorkerState.STARTING, FleetWorkerState.HEALTHY}
    }


def _cleanup_pending(health) -> bool:
    cleanup_errors = {
        "child_termination_timeout",
        "worker_lease_release_failed",
    }
    return any(
        item.state in {FleetWorkerState.STARTING, FleetWorkerState.HEALTHY}
        or item.error_code in cleanup_errors
        for item in health
    )


def run_acquisition_fleet(
    repository: AcquisitionRepository,
    round_id: str,
    config: FleetConfig,
    *,
    poll_interval_seconds: float = 0.5,
    event_factory: Callable[[], StopEvent] | None = None,
    relay_factory: Callable[..., object] = ProxyRelay,
    supervisor_factory: Callable[..., object] = FleetSupervisor,
    launcher_factory: Callable[[Path, str, StopEvent], object] | None = None,
    clock_ms: Callable[[], int] = _clock_ms,
    sleeper: Callable[[float], None] = time.sleep,
    restart_backoff_seconds: float = 1.0,
    max_device_restart_attempts: int = 3,
    cleanup_poll_interval_seconds: float = 0.1,
    cleanup_max_attempts: int = 3,
) -> int:
    if restart_backoff_seconds < 0:
        raise ValueError("fleet restart backoff must be nonnegative")
    if max_device_restart_attempts < 0:
        raise ValueError("fleet restart attempt count must be nonnegative")
    if cleanup_poll_interval_seconds < 0:
        raise ValueError("fleet cleanup poll interval must be nonnegative")
    if cleanup_max_attempts <= 0:
        raise ValueError("fleet cleanup attempt count must be positive")
    completion = repository.round_completion(round_id)
    if _round_completed(completion):
        return 0
    repository.recover_expired_assignment_leases(now_ms=clock_ms())

    context = None
    if event_factory is None or launcher_factory is None:
        context = multiprocessing.get_context("spawn")
    stop_event = event_factory() if event_factory is not None else context.Event()
    launcher = (
        launcher_factory(repository.path, round_id, stop_event)
        if launcher_factory is not None
        else _process_launcher(context, repository.path, round_id, stop_event)
    )
    relay = relay_factory(
        config.relay_bind_host,
        config.relay_bind_port,
        config.relay_upstream_host,
        config.relay_upstream_port,
        allowed_sources=config.relay_allowed_sources,
    )
    supervisor = supervisor_factory(
        repository,
        config,
        launcher=launcher,
        clock_ms=clock_ms,
    )
    configured_device_ids = tuple(device.device_id for device in config.devices)
    restart_attempts = {device_id: 0 for device_id in configured_device_ids}
    next_restart_at_ms: dict[str, int] = {}
    release_failed_device_ids: set[str] = set()

    def observe_health(health):
        observed = tuple(health)
        release_failed_device_ids.update(
            item.device_id
            for item in observed
            if item.error_code == "worker_lease_release_failed"
        )
        return observed

    def schedule_inactive(health, *, now_ms: int) -> tuple[str, ...] | None:
        active_device_ids = _active_device_ids(health)
        inactive_device_ids = tuple(
            device_id
            for device_id in configured_device_ids
            if device_id not in active_device_ids
            and device_id not in release_failed_device_ids
        )
        for device_id in configured_device_ids:
            if device_id in active_device_ids or device_id in release_failed_device_ids:
                next_restart_at_ms.pop(device_id, None)
        if release_failed_device_ids:
            return None
        if any(
            restart_attempts[device_id] >= max_device_restart_attempts
            for device_id in inactive_device_ids
        ):
            return None
        for device_id in inactive_device_ids:
            delay_ms = int(
                restart_backoff_seconds * 1_000 * (2 ** restart_attempts[device_id])
            )
            next_restart_at_ms.setdefault(device_id, now_ms + delay_ms)
        return tuple(
            device_id
            for device_id in inactive_device_ids
            if now_ms >= next_restart_at_ms[device_id]
        )

    result = 1
    with relay:
        try:
            health = observe_health(supervisor.start())
            now_ms = clock_ms()
            due_device_ids = schedule_inactive(health, now_ms=now_ms)
            if due_device_ids is not None:
                while True:
                    completion = repository.round_completion(round_id)
                    if _round_completed(completion):
                        result = 0
                        break
                    sleeper(poll_interval_seconds)
                    now_ms = clock_ms()
                    repository.recover_expired_assignment_leases(now_ms=now_ms)
                    health = observe_health(supervisor.poll())
                    due_device_ids = schedule_inactive(health, now_ms=now_ms)
                    if due_device_ids is None:
                        break
                    if not due_device_ids:
                        continue
                    for device_id in due_device_ids:
                        restart_attempts[device_id] += 1
                        next_restart_at_ms.pop(device_id, None)
                    health = observe_health(supervisor.restart_devices(due_device_ids))
                    due_device_ids = schedule_inactive(health, now_ms=now_ms)
                    if due_device_ids is None:
                        break
        except KeyboardInterrupt:
            result = 130
        finally:
            stop_event.set()
            cleanup_health = observe_health(supervisor.stop())
            for _ in range(cleanup_max_attempts):
                if not _cleanup_pending(cleanup_health):
                    break
                sleeper(cleanup_poll_interval_seconds)
                cleanup_health = observe_health(supervisor.poll())
            if result == 0 and (
                release_failed_device_ids or _cleanup_pending(cleanup_health)
            ):
                result = 1
    return result

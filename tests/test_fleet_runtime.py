import threading
from pathlib import Path

import pytest

from tikpoc.acquisition_models import (
    AssignmentPhase,
    DeviceDiagnostics,
    OutcomeKind,
    RoundCompletion,
)
from tikpoc.fleet import (
    FleetConfig,
    FleetWorkerHealth,
    FleetWorkerState,
)
from tikpoc.fleet_runtime import (
    FencedVerifiedDevice,
    run_acquisition_fleet,
    run_device_worker,
)

from tests.test_fleet import _two_device_config


class RecordingFence:
    owner_id = "worker-phone-01"

    def __init__(self) -> None:
        self.operations: list[str] = []
        self.assertions = 0

    def assert_active(self) -> None:
        self.assertions += 1

    def execute(self, operation, *args, **kwargs):
        self.assert_active()
        self.operations.append(operation.__name__)
        return operation(*args, **kwargs)


class ProtocolDevice:
    def ensure_ready(self) -> None:
        return None

    def open_target(self, target) -> None:
        self.target = target

    def confirm_profile_identity(self, target) -> None:
        self.identity = target

    def read_profile_observation(self):
        return "observation"

    def list_video_keys(self) -> tuple[str, ...]:
        return ("video-1",)

    def open_and_confirm_video(self, video_key: str) -> None:
        self.video_key = video_key

    def execute_outcome(self, outcome: OutcomeKind):
        return outcome

    def reconcile_outcome(self, outcome: OutcomeKind):
        return outcome

    def capture_diagnostics(self) -> DeviceDiagnostics:
        return DeviceDiagnostics(ui_summary="visible")

    def recover(self, phase: AssignmentPhase) -> None:
        self.phase = phase


def test_fenced_device_checks_every_device_side_effect() -> None:
    fence = RecordingFence()
    device = FencedVerifiedDevice(ProtocolDevice(), fence)

    device.ensure_ready()
    device.open_target("target")
    device.confirm_profile_identity("target")
    assert device.read_profile_observation() == "observation"
    assert device.list_video_keys() == ("video-1",)
    device.open_and_confirm_video("video-1")
    assert device.execute_outcome(OutcomeKind.LIKE) is OutcomeKind.LIKE
    assert device.reconcile_outcome(OutcomeKind.LIKE) is OutcomeKind.LIKE
    assert device.capture_diagnostics().ui_summary == "visible"
    device.recover(AssignmentPhase.VIDEO_OPENING)

    assert fence.operations == [
        "ensure_ready",
        "open_target",
        "confirm_profile_identity",
        "read_profile_observation",
        "list_video_keys",
        "open_and_confirm_video",
        "execute_outcome",
        "reconcile_outcome",
        "capture_diagnostics",
        "recover",
    ]


def test_device_worker_processes_assignments_until_stopped(tmp_path: Path) -> None:
    stop_event = threading.Event()
    fence = RecordingFence()
    assignment = object()

    class FakeRepository:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.claims = 0

        def claim_next_assignment(self, *args, **kwargs):
            self.claims += 1
            return assignment

    repositories: list[FakeRepository] = []

    def repository_factory(path: Path) -> FakeRepository:
        repository = FakeRepository(path)
        repositories.append(repository)
        return repository

    class FakeDriver:
        def __init__(self) -> None:
            self.closed = False

        def quit(self) -> None:
            self.closed = True

    driver = FakeDriver()
    captured = {}

    class FakeWorker:
        def __init__(self, repository, device, **kwargs) -> None:
            captured["repository"] = repository
            captured["device"] = device
            captured.update(kwargs)

        def run_assignment(self, observed_assignment) -> None:
            captured["assignment"] = observed_assignment
            stop_event.set()

    config = _two_device_config(tmp_path)
    run_device_worker(
        tmp_path / "acquisition.db",
        "round-1",
        config.devices[0],
        fence,
        stop_event,
        idle_sleep_seconds=0.001,
        repository_factory=repository_factory,
        driver_factory=lambda appium_url, adb_endpoint: driver,
        device_factory=lambda driver: ProtocolDevice(),
        worker_factory=FakeWorker,
        clock_ms=lambda: 1_000,
    )

    assert repositories[0].claims == 1
    assert fence.assertions == 2
    assert captured["assignment"] is assignment
    assert captured["owner_id"] == fence.owner_id
    assert isinstance(captured["device"], FencedVerifiedDevice)
    assert driver.closed is True


def test_device_worker_checks_fence_before_creating_appium_session(
    tmp_path: Path,
) -> None:
    class LostFence(RecordingFence):
        def assert_active(self) -> None:
            raise RuntimeError("worker lease lost")

    driver_created = False

    def create_driver(appium_url: str, adb_endpoint: str):
        nonlocal driver_created
        driver_created = True
        return object()

    config = _two_device_config(tmp_path)
    with pytest.raises(RuntimeError, match="worker lease lost"):
        run_device_worker(
            tmp_path / "acquisition.db",
            "round-1",
            config.devices[0],
            LostFence(),
            threading.Event(),
            repository_factory=lambda path: object(),
            driver_factory=create_driver,
        )

    assert driver_created is False


class FakeStopEvent:
    def __init__(self) -> None:
        self.is_set_value = False

    def set(self) -> None:
        self.is_set_value = True


class FakeRelay:
    def __init__(self, *args, **kwargs) -> None:
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args) -> None:
        self.exited = True


def _health(state: FleetWorkerState) -> tuple[FleetWorkerHealth, ...]:
    return (
        FleetWorkerHealth(
            device_id="phone-01",
            account_id="account-01",
            state=state,
            owner_id="worker-phone-01",
            fence_token=1,
            process_id=101,
            error_code=None,
            updated_at_ms=1_000,
        ),
    )


def _fleet_health(
    phone_01: FleetWorkerState, phone_02: FleetWorkerState
) -> tuple[FleetWorkerHealth, ...]:
    return tuple(
        FleetWorkerHealth(
            device_id=device_id,
            account_id=account_id,
            state=state,
            owner_id=f"worker-{device_id}",
            fence_token=index,
            process_id=100 + index,
            error_code=None if state is FleetWorkerState.HEALTHY else "child_exit_1",
            updated_at_ms=1_000,
        )
        for index, (device_id, account_id, state) in enumerate(
            (
                ("phone-01", "account-01", phone_01),
                ("phone-02", "account-02", phone_02),
            ),
            start=1,
        )
    )


def test_fleet_runtime_stops_all_resources_after_round_completion(
    tmp_path: Path,
) -> None:
    config: FleetConfig = _two_device_config(tmp_path)
    stop_event = FakeStopEvent()
    relay = FakeRelay()

    class FakeRepository:
        path = tmp_path / "tikpoc.db"

        def __init__(self) -> None:
            self.completions = iter(
                (
                    RoundCompletion(
                        total=2, visits_confirmed=0, completed=0, deferred=0
                    ),
                    RoundCompletion(
                        total=2, visits_confirmed=2, completed=2, deferred=0
                    ),
                )
            )

        def round_completion(self, round_id: str) -> RoundCompletion:
            return next(self.completions)

        def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
            return 0

    class FakeSupervisor:
        def __init__(self, *args, **kwargs) -> None:
            self.stopped = False

        def start(self):
            return _health(FleetWorkerState.HEALTHY)

        def poll(self):
            return _health(FleetWorkerState.HEALTHY)

        def stop(self):
            self.stopped = True
            return _health(FleetWorkerState.STOPPED)

    supervisor = FakeSupervisor()

    result = run_acquisition_fleet(
        FakeRepository(),
        "round-1",
        config,
        event_factory=lambda: stop_event,
        relay_factory=lambda *args, **kwargs: relay,
        supervisor_factory=lambda *args, **kwargs: supervisor,
        launcher_factory=lambda *args, **kwargs: object(),
        clock_ms=lambda: 1_000,
        sleeper=lambda seconds: None,
    )

    assert result == 0
    assert stop_event.is_set_value is True
    assert supervisor.stopped is True
    assert relay.entered is True
    assert relay.exited is True


def test_fleet_runtime_fails_when_no_worker_is_healthy(tmp_path: Path) -> None:
    config = _two_device_config(tmp_path)
    stop_event = FakeStopEvent()

    class FakeRepository:
        path = tmp_path / "tikpoc.db"

        def round_completion(self, round_id: str) -> RoundCompletion:
            return RoundCompletion(total=2, visits_confirmed=0, completed=0, deferred=0)

        def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
            return 0

    class FakeSupervisor:
        def start(self):
            return _health(FleetWorkerState.UNHEALTHY)

        def stop(self):
            return _health(FleetWorkerState.STOPPED)

    result = run_acquisition_fleet(
        FakeRepository(),
        "round-1",
        config,
        event_factory=lambda: stop_event,
        relay_factory=lambda *args, **kwargs: FakeRelay(),
        supervisor_factory=lambda *args, **kwargs: FakeSupervisor(),
        launcher_factory=lambda *args, **kwargs: object(),
        clock_ms=lambda: 1_000,
        sleeper=lambda seconds: None,
        max_device_restart_attempts=0,
    )

    assert result == 1
    assert stop_event.is_set_value is True


def test_fleet_runtime_recovers_leases_and_restarts_only_failed_device(
    tmp_path: Path,
) -> None:
    config = _two_device_config(tmp_path)
    stop_event = FakeStopEvent()

    class FakeRepository:
        path = tmp_path / "tikpoc.db"

        def __init__(self) -> None:
            self.completed = False
            self.recovery_calls: list[int] = []

        def round_completion(self, round_id: str) -> RoundCompletion:
            completed = 2 if self.completed else 0
            return RoundCompletion(
                total=2,
                visits_confirmed=completed,
                completed=completed,
                deferred=0,
            )

        def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
            self.recovery_calls.append(now_ms)
            return 0

    repository = FakeRepository()

    class FakeSupervisor:
        def __init__(self) -> None:
            self.health = _fleet_health(
                FleetWorkerState.UNHEALTHY, FleetWorkerState.HEALTHY
            )
            self.restarts: list[tuple[str, ...]] = []
            self.poll_calls = 0

        def start(self):
            return self.health

        def poll(self):
            self.poll_calls += 1
            return self.health

        def restart_devices(self, device_ids):
            self.restarts.append(tuple(device_ids))
            self.health = _fleet_health(
                FleetWorkerState.HEALTHY, FleetWorkerState.HEALTHY
            )
            repository.completed = True
            return self.health

        def stop(self):
            return _fleet_health(FleetWorkerState.STOPPED, FleetWorkerState.STOPPED)

    supervisor = FakeSupervisor()
    observed_at_ms = 1_000

    def clock_ms() -> int:
        nonlocal observed_at_ms
        observed_at_ms += 20
        return observed_at_ms

    result = run_acquisition_fleet(
        repository,
        "round-1",
        config,
        event_factory=lambda: stop_event,
        relay_factory=lambda *args, **kwargs: FakeRelay(),
        supervisor_factory=lambda *args, **kwargs: supervisor,
        launcher_factory=lambda *args, **kwargs: object(),
        clock_ms=clock_ms,
        sleeper=lambda seconds: None,
        restart_backoff_seconds=0.05,
    )

    assert result == 0
    assert supervisor.restarts == [("phone-01",)]
    assert supervisor.poll_calls >= 3
    assert len(repository.recovery_calls) >= 2


def test_fleet_runtime_stops_after_device_restart_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    config = _two_device_config(tmp_path)

    class FakeRepository:
        path = tmp_path / "tikpoc.db"

        def round_completion(self, round_id: str) -> RoundCompletion:
            return RoundCompletion(total=2, visits_confirmed=0, completed=0, deferred=0)

        def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
            return 0

    class FakeSupervisor:
        def __init__(self) -> None:
            self.restarts: list[tuple[str, ...]] = []

        def start(self):
            return _fleet_health(FleetWorkerState.UNHEALTHY, FleetWorkerState.HEALTHY)

        def poll(self):
            return _fleet_health(FleetWorkerState.UNHEALTHY, FleetWorkerState.HEALTHY)

        def restart_devices(self, device_ids):
            self.restarts.append(tuple(device_ids))
            return self.poll()

        def stop(self):
            return _fleet_health(FleetWorkerState.STOPPED, FleetWorkerState.STOPPED)

    supervisor = FakeSupervisor()
    observed_at_ms = 1_000

    def clock_ms() -> int:
        nonlocal observed_at_ms
        observed_at_ms += 100
        return observed_at_ms

    result = run_acquisition_fleet(
        FakeRepository(),
        "round-1",
        config,
        event_factory=FakeStopEvent,
        relay_factory=lambda *args, **kwargs: FakeRelay(),
        supervisor_factory=lambda *args, **kwargs: supervisor,
        launcher_factory=lambda *args, **kwargs: object(),
        clock_ms=clock_ms,
        sleeper=lambda seconds: None,
        restart_backoff_seconds=0.05,
        max_device_restart_attempts=2,
    )

    assert result == 1
    assert supervisor.restarts == [("phone-01",), ("phone-01",)]


def test_fleet_runtime_keeps_workers_running_for_retryable_deferred_work(
    tmp_path: Path,
) -> None:
    config = _two_device_config(tmp_path)

    class FakeRepository:
        path = tmp_path / "tikpoc.db"

        def __init__(self) -> None:
            self.completed = False

        def round_completion(self, round_id: str) -> RoundCompletion:
            if self.completed:
                return RoundCompletion(
                    total=2, visits_confirmed=2, completed=2, deferred=0
                )
            return RoundCompletion(total=2, visits_confirmed=1, completed=1, deferred=1)

        def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
            return 0

    repository = FakeRepository()

    class FakeSupervisor:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True
            repository.completed = True
            return _fleet_health(FleetWorkerState.HEALTHY, FleetWorkerState.HEALTHY)

        def stop(self):
            self.stopped = True
            return _fleet_health(FleetWorkerState.STOPPED, FleetWorkerState.STOPPED)

    supervisor = FakeSupervisor()

    result = run_acquisition_fleet(
        repository,
        "round-1",
        config,
        event_factory=FakeStopEvent,
        relay_factory=lambda *args, **kwargs: FakeRelay(),
        supervisor_factory=lambda *args, **kwargs: supervisor,
        launcher_factory=lambda *args, **kwargs: object(),
    )

    assert result == 0
    assert supervisor.started is True
    assert supervisor.stopped is True


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"restart_backoff_seconds": -0.1}, "restart backoff"),
        ({"max_device_restart_attempts": -1}, "restart attempt count"),
    ),
)
def test_fleet_runtime_rejects_invalid_restart_controls(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    class CompletedRepository:
        path = tmp_path / "tikpoc.db"

        def round_completion(self, round_id: str) -> RoundCompletion:
            return RoundCompletion(total=1, visits_confirmed=1, completed=1, deferred=0)

    with pytest.raises(ValueError, match=message):
        run_acquisition_fleet(
            CompletedRepository(),
            "round-1",
            _two_device_config(tmp_path),
            **overrides,
        )

import sqlite3
from collections import deque
from pathlib import Path

import pytest
from selenium.common.exceptions import WebDriverException

from tests.test_appium_device import BoundedVideoDriver
from tikpoc.acquisition_db import AcquisitionRepository, DeviceWorkerLeaseLost
from tikpoc.acquisition_models import (
    ActionPlanState,
    ActionResult,
    AssignmentPhase,
    AssignmentStage,
    DeviceDiagnostics,
    OutcomeKind,
    ProfileAccessState,
    ProfileObservation,
)
from tikpoc.device import AppiumTikTokDevice, ProfileIdentityMismatch
from tikpoc.device_performance import DevicePerformanceSnapshot
from tikpoc.importer import Target
from tikpoc.mobile_routes import AdbRouteError
from tikpoc.mobile_worker import MobileAssignmentWorker
from tikpoc.models import ProfileMetrics
from tikpoc.outcome_planner import get_or_create_plan
from tikpoc.rounds import create_exposure_round

MANUAL_RETRY_AT_MS = 9_223_372_036_854_775_807


class ScriptedVerifiedDevice:
    def __init__(
        self,
        *,
        metrics: ProfileMetrics,
        action_results: list[ActionResult] | None = None,
        video_keys: tuple[str, ...] = ("video-a", "video-b"),
    ) -> None:
        self.metrics = metrics
        self.action_results = deque(action_results or [])
        self.video_keys = video_keys
        self.opened_profiles: list[str] = []
        self.opened_videos: list[str] = []
        self.action_calls: list[OutcomeKind] = []
        self.reconcile_calls: list[OutcomeKind] = []
        self.recovery_calls: list[AssignmentPhase] = []
        self.diagnostic_calls = 0

    def ensure_ready(self) -> None:
        return

    def open_target(self, target) -> None:
        self.opened_profiles.append(target.username)

    def confirm_profile_identity(self, target) -> None:
        return

    def read_profile_observation(self) -> ProfileObservation:
        return ProfileObservation(
            observed_username="buyer",
            metrics=self.metrics,
            private_account=False,
            access_state=ProfileAccessState.PUBLIC,
        )

    def list_video_keys(self) -> tuple[str, ...]:
        return self.video_keys

    def open_and_confirm_video(self, video_key: str) -> None:
        self.opened_videos.append(video_key)

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        self.action_calls.append(outcome)
        return self.action_results.popleft()

    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult:
        self.reconcile_calls.append(outcome)
        return self.action_results.popleft()

    def capture_diagnostics(self) -> DeviceDiagnostics:
        self.diagnostic_calls += 1
        return DeviceDiagnostics(
            screenshot_path="screenshots/assignment.png",
            ui_summary="share surface still loading",
        )

    def recover(self, phase: AssignmentPhase) -> None:
        self.recovery_calls.append(phase)


class MutableClock:
    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, milliseconds: int) -> None:
        self.now_ms += milliseconds


class TimedScriptedDevice(ScriptedVerifiedDevice):
    def __init__(self, clock: MutableClock) -> None:
        super().__init__(
            metrics=ProfileMetrics(20, 10, 5),
            action_results=[ActionResult.CONFIRMED],
        )
        self.clock = clock
        self.commands = 0

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        return DevicePerformanceSnapshot(
            command_count=self.commands,
            command_duration_ms=self.commands * 50,
            page_source_reads=self.commands,
        )

    def open_target(self, target) -> None:
        super().open_target(target)
        self.commands += 1
        self.clock.advance(100)

    def confirm_profile_identity(self, target) -> None:
        self.commands += 2
        self.clock.advance(200)

    def read_profile_observation(self) -> ProfileObservation:
        self.commands += 3
        self.clock.advance(300)
        return super().read_profile_observation()

    def open_and_confirm_video(self, video_key: str) -> None:
        super().open_and_confirm_video(video_key)
        self.commands += 4
        self.clock.advance(400)

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        self.commands += 5
        self.clock.advance(500)
        return super().execute_outcome(outcome)


class FailingTimedRouteDevice(TimedScriptedDevice):
    def open_target(self, target) -> None:
        super().open_target(target)
        raise RuntimeError("route unavailable")


class SharedSnapshotDelayedGridDevice:
    def __init__(self) -> None:
        class DelayedGridDriver(BoundedVideoDriver):
            def find_elements(self, by: str, value: str):
                if (
                    by == "xpath"
                    and "com.zhiliaoapp.musically:id/cover" in value
                    and self.post_queries == 0
                ):
                    self.post_queries += 1
                    return []
                return super().find_elements(by, value)

        self.driver = DelayedGridDriver()
        self.appium = AppiumTikTokDevice(
            self.driver, metric_read_attempts=2, poll_interval=0
        )
        self.observation_reads = 0

    def ensure_ready(self) -> None:
        return

    def open_target(self, target) -> None:
        return

    def confirm_profile_identity(self, target) -> None:
        return

    def read_profile_observation(self) -> ProfileObservation:
        self.observation_reads += 1
        raise AssertionError("shared snapshot should skip profile observation")

    def list_video_keys(self) -> tuple[str, ...]:
        return self.appium.list_video_keys()

    def open_and_confirm_video(self, video_key: str) -> None:
        self.appium.open_and_confirm_video(video_key)

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        raise AssertionError("trace plan should not execute an interaction")

    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult:
        raise AssertionError("trace plan should not reconcile an interaction")

    def capture_diagnostics(self) -> DeviceDiagnostics:
        return DeviceDiagnostics(screenshot_path="", ui_summary="")

    def recover(self, phase: AssignmentPhase) -> None:
        return


def _claimed_assignment(
    tmp_path: Path,
) -> tuple[AcquisitionRepository, object]:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    target = Target(
        target_id="user-1",
        username="buyer",
        profile_url="https://www.tiktok.com/@buyer",
        source_video_id="video-source",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = repository.import_pool("comments.csv", "1" * 64, (target,))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "seed-a"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
    )
    assignment = repository.claim_next_assignment(
        round_id,
        "phone-01",
        "worker-1",
        now_ms=1_000,
    )
    assert assignment is not None
    return repository, assignment


def _forced_plan(outcome: OutcomeKind):
    def provider(
        repository: AcquisitionRepository,
        round_id: str,
        identity_key: str,
        device_id: str,
        now_ms: int,
        **worker_fence: object,
    ):
        return get_or_create_plan(
            repository,
            round_id,
            identity_key,
            device_id,
            now_ms=now_ms,
            forced_draw=outcome,
            **worker_fence,
        )

    return provider


def test_worker_does_not_complete_until_visible_action_is_confirmed(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNCERTAIN, ActionResult.CONFIRMED],
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert device.opened_profiles == ["buyer"]
    assert device.action_calls == [OutcomeKind.LIKE]
    assert device.reconcile_calls == [OutcomeKind.LIKE]
    assert repository.round_completion(assignment.round_id).completed == 1


def test_worker_passes_device_fence_to_plan_creation(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=1_000
    )
    assert isinstance(token, int)
    observed_fence: dict[str, object] = {}

    def fenced_plan_provider(
        repository: AcquisitionRepository,
        round_id: str,
        identity_key: str,
        device_id: str,
        now_ms: int,
        **worker_fence: object,
    ):
        observed_fence.update(worker_fence)
        return get_or_create_plan(
            repository,
            round_id,
            identity_key,
            device_id,
            now_ms=now_ms,
            forced_draw=OutcomeKind.TRACE,
            **worker_fence,
        )

    worker = MobileAssignmentWorker(
        repository,
        ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 1)),
        device_id="phone-01",
        owner_id="worker-1",
        worker_account_id="account-01",
        worker_fence_token=token,
        clock_ms=lambda: 1_000,
        plan_provider=fenced_plan_provider,
    )

    worker.run_assignment(assignment)

    assert observed_fence == {
        "worker_owner_id": "worker-1",
        "worker_account_id": "account-01",
        "worker_fence_token": token,
    }


def test_worker_binds_current_phase_to_device_commands(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=1_000
    )
    assert isinstance(token, int)

    class BoundDevice(ScriptedVerifiedDevice):
        def __init__(self) -> None:
            super().__init__(
                metrics=ProfileMetrics(20, 10, 5),
                action_results=[ActionResult.UNCERTAIN, ActionResult.CONFIRMED],
            )
            self.bindings: list[tuple[int, AssignmentPhase, str, int]] = []

        def bind_assignment(
            self,
            assignment_id: int,
            phase: AssignmentPhase,
            *,
            account_id: str,
            fence_token: int,
        ) -> None:
            self.bindings.append((assignment_id, phase, account_id, fence_token))

    device = BoundDevice()
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        worker_account_id="account-01",
        worker_fence_token=token,
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    assert [binding[1] for binding in device.bindings] == [
        AssignmentPhase.PROFILE_OPENING,
        AssignmentPhase.VIDEO_OPENING,
        AssignmentPhase.ACTION_EXECUTING,
        AssignmentPhase.ACTION_RECONCILING,
    ]
    assert all(
        binding[0] == assignment.assignment_id and binding[2:] == ("account-01", token)
        for binding in device.bindings
    )


def test_unfenced_worker_keeps_legacy_five_argument_plan_provider(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    calls = 0

    def legacy_provider(
        repository: AcquisitionRepository,
        round_id: str,
        identity_key: str,
        device_id: str,
        now_ms: int,
    ):
        nonlocal calls
        calls += 1
        return get_or_create_plan(
            repository,
            round_id,
            identity_key,
            device_id,
            now_ms=now_ms,
            forced_draw=OutcomeKind.TRACE,
        )

    worker = MobileAssignmentWorker(
        repository,
        ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 1)),
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=legacy_provider,
    )

    worker.run_assignment(assignment)

    assert calls == 1
    assert repository.assignment(assignment.assignment_id).phase is (
        AssignmentPhase.COMPLETED
    )


def test_worker_persists_route_identity_metrics_video_and_action_timings(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    clock = MutableClock()
    worker = MobileAssignmentWorker(
        repository,
        TimedScriptedDevice(clock),
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=clock,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    timings = repository.assignment_stage_timings(assignment.assignment_id)
    assert {timing.stage: timing.duration_ms for timing in timings} == {
        AssignmentStage.ROUTE: 100,
        AssignmentStage.IDENTITY: 200,
        AssignmentStage.METRICS: 300,
        AssignmentStage.VIDEO: 400,
        AssignmentStage.ACTION: 500,
    }
    metrics = repository.assignment_command_metrics(assignment.assignment_id)
    assert {item.stage: item.command_count for item in metrics} == {
        AssignmentStage.ROUTE: 1,
        AssignmentStage.IDENTITY: 2,
        AssignmentStage.METRICS: 3,
        AssignmentStage.VIDEO: 4,
        AssignmentStage.ACTION: 5,
    }


def test_worker_records_command_metrics_for_deferred_route_failure(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    clock = MutableClock()
    worker = MobileAssignmentWorker(
        repository,
        FailingTimedRouteDevice(clock),
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=clock,
    )

    worker.run_assignment(assignment)

    assert (
        repository.assignment(assignment.assignment_id).phase
        is AssignmentPhase.DEFERRED
    )
    assert {
        timing.stage: timing.duration_ms
        for timing in repository.assignment_stage_timings(assignment.assignment_id)
    } == {AssignmentStage.ROUTE: 100}
    assert {
        metric.stage: metric.command_count
        for metric in repository.assignment_command_metrics(assignment.assignment_id)
    } == {AssignmentStage.ROUTE: 1}


@pytest.mark.parametrize(
    "error",
    [AdbRouteError("ADB route failed"), WebDriverException("session unavailable")],
)
def test_worker_defers_then_propagates_device_transport_failure(
    tmp_path: Path, error: Exception
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def fail_route(target) -> None:
        raise error

    device.open_target = fail_route
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    with pytest.raises(type(error)):
        worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == type(error).__name__


def test_assignment_stage_timing_replaces_the_previous_attempt(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    repository.record_assignment_stage_timing(
        assignment.assignment_id,
        AssignmentStage.ROUTE,
        duration_ms=900,
        recorded_at_ms=2_000,
    )

    latest = repository.record_assignment_stage_timing(
        assignment.assignment_id,
        AssignmentStage.ROUTE,
        duration_ms=120,
        recorded_at_ms=3_000,
    )

    assert repository.assignment_stage_timings(assignment.assignment_id) == (latest,)
    with pytest.raises(ValueError, match="nonnegative"):
        repository.record_assignment_stage_timing(
            assignment.assignment_id,
            AssignmentStage.IDENTITY,
            duration_ms=-1,
            recorded_at_ms=3_000,
        )


def test_persistent_uncertain_action_completes_after_one_reconciliation_without_reclick(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNCERTAIN] * 4,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.REPOST),
        max_action_attempts=3,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    assert stored.phase is AssignmentPhase.COMPLETED
    assert stored.next_attempt_at_ms == 0
    assert stored.completed_at_ms == 1_000
    assert stored.last_error_code == "action_uncertain_terminal"
    assert repository.round_completion(assignment.round_id).completed == 1
    assert plan is not None and plan.state is ActionPlanState.UNCERTAIN
    assert device.action_calls == [OutcomeKind.REPOST]
    assert device.reconcile_calls == [OutcomeKind.REPOST]
    assert device.diagnostic_calls == 0


def test_confirmed_visit_profile_unreachable_completes_as_explicit_failure(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "UPDATE round_assignments SET visit_confirmed_at_ms = ? "
            "WHERE assignment_id = ?",
            (900, assignment.assignment_id),
        )

    class UnreachableAfterConfirmedVisitDevice(ScriptedVerifiedDevice):
        def confirm_profile_identity(self, target) -> None:
            raise ValueError("profile route stayed blank")

    device = UnreachableAfterConfirmedVisitDevice(metrics=ProfileMetrics(20, 10, 5))
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.MISSING,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert stored.visit_confirmed_at_ms == 900
    assert stored.completed_at_ms == 1_000
    assert stored.next_attempt_at_ms == 0
    assert stored.last_error_code == "confirmed_visit_profile_unreachable"


def test_uncertain_action_never_returns_to_execution_after_reconciliation(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    first_device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNCERTAIN, ActionResult.UNCERTAIN],
    )
    first_worker = MobileAssignmentWorker(
        repository,
        first_device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.REPOST),
        max_action_attempts=1,
    )
    first_worker.run_assignment(assignment)
    first_plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    first_quota = repository.quota_window("phone-01", OutcomeKind.REPOST, 0)
    assert first_plan is not None and first_plan.state is ActionPlanState.UNCERTAIN
    assert first_device.reconcile_calls == [OutcomeKind.REPOST]
    assert first_quota is not None and first_quota.uncertain_count == 1

    stored = repository.assignment(assignment.assignment_id)
    final_plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    final_quota = repository.quota_window("phone-01", OutcomeKind.REPOST, 0)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert stored.last_error_code == "action_uncertain_terminal"
    assert final_plan is not None and final_plan.state is ActionPlanState.UNCERTAIN
    assert first_device.action_calls == [OutcomeKind.REPOST]
    assert first_device.reconcile_calls == [OutcomeKind.REPOST]
    assert first_device.diagnostic_calls == 0
    assert final_quota is not None
    assert final_quota.reserved_count == 1
    assert final_quota.confirmed_count == 0
    assert final_quota.uncertain_count == 1


def test_reconciled_unavailable_like_completes_as_explicit_uncertain(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    first_device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNCERTAIN, ActionResult.UNAVAILABLE],
    )
    first_worker = MobileAssignmentWorker(
        repository,
        first_device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
        max_action_attempts=1,
    )
    first_worker.run_assignment(assignment)

    final_plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    stored = repository.assignment(assignment.assignment_id)
    quota = repository.quota_window("phone-01", OutcomeKind.LIKE, 0)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert stored.last_error_code == "action_uncertain_terminal"
    assert final_plan is not None and final_plan.state is ActionPlanState.UNCERTAIN
    assert first_device.action_calls == [OutcomeKind.LIKE]
    assert first_device.reconcile_calls == [OutcomeKind.LIKE]
    assert quota is not None
    assert quota.reserved_count == 1
    assert quota.confirmed_count == 0
    assert quota.uncertain_count == 1


def test_video_verification_failure_is_durably_deferred(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def missing_controls(video_key: str) -> None:
        device.opened_videos.append(video_key)
        raise RuntimeError("video controls did not become visible")

    device.open_and_confirm_video = missing_controls
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "RuntimeError"
    assert stored.completed_at_ms is None
    assert repository.round_completion(assignment.round_id).completed == 0


def test_lost_device_fence_escapes_without_stale_defer_write(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    first_token = repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-1", now_ms=1_000, ttl_ms=100
    )
    assert isinstance(first_token, int)
    repository.claim_device_worker_lease(
        "phone-01", "account-01", "worker-2", now_ms=1_100, ttl_ms=100
    )
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 1))

    def lost_during_route(target) -> None:
        raise DeviceWorkerLeaseLost("device worker fence is inactive for phone-01")

    device.open_target = lost_during_route
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        worker_account_id="account-01",
        worker_fence_token=first_token,
        clock_ms=lambda: 1_101,
    )

    with pytest.raises(DeviceWorkerLeaseLost):
        worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.PROFILE_OPENING
    assert stored.lease_owner == "worker-1"
    assert stored.last_error_code is None
    assert device.diagnostic_calls == 0


def test_visible_unavailable_action_completes_as_trace_fallback(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNAVAILABLE],
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.REPOST),
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    quota = repository.quota_window("phone-01", OutcomeKind.REPOST, 0)
    assert stored.phase is AssignmentPhase.COMPLETED
    assert plan is not None
    assert plan.requested_outcome is OutcomeKind.REPOST
    assert plan.effective_outcome is OutcomeKind.TRACE
    assert plan.quota_reason == "repost_unavailable"
    assert plan.state is ActionPlanState.CONFIRMED
    assert quota is not None and quota.reserved_count == 0
    assert device.action_calls == [OutcomeKind.REPOST]
    assert device.reconcile_calls == []


def test_identity_mismatch_is_durable_and_blocks_capacity(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def mismatch(target) -> None:
        raise ProfileIdentityMismatch("profile mismatch")

    device.confirm_profile_identity = mismatch
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    audit = repository.capacity_audit(assignment.round_id, expected_devices=1)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ProfileIdentityMismatch"
    assert audit.identity_mismatch_count == 1
    assert repository.round_completion(assignment.round_id).completed == 0
    assert device.action_calls == []
    assert device.diagnostic_calls == 1


def test_profile_route_value_error_without_unavailable_page_is_deferred(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def unreachable(target) -> None:
        raise ValueError("stable and username profile routes did not load")

    device.open_target = unreachable
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.attempt_count == 1
    assert stored.visit_confirmed_at_ms is None
    assert stored.last_error_code == "ValueError"
    assert repository.round_completion(assignment.round_id).skipped == 0
    assert device.diagnostic_calls == 1


def test_explicit_missing_profile_skips_after_first_identity_failure(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(0, 0, 0))

    def missing_identity(target) -> None:
        raise ValueError("stable and username profile routes did not load")

    device.confirm_profile_identity = missing_identity
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.MISSING,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.SKIPPED
    assert stored.last_error_code == "profile_unreachable"
    transition = repository.assignment_phase_history(assignment.assignment_id)[-1]
    assert transition.details["original_error_code"] == "ValueError"
    assert transition.details["failure_stage"] == "identity"


def test_transient_inaccessible_identity_failure_is_deferred(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(0, 0, 0))

    def incomplete_identity(target) -> None:
        raise ValueError("profile surface did not become ready")

    device.confirm_profile_identity = incomplete_identity
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.INACCESSIBLE,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ValueError"


@pytest.mark.parametrize("observed_username", ["", "previous_target"])
def test_unavailable_page_without_current_target_identity_is_deferred(
    tmp_path: Path, observed_username: str
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(0, 0, 0))
    device.confirm_profile_identity = lambda target: (_ for _ in ()).throw(
        ValueError("profile did not load")
    )
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username=observed_username,
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.MISSING,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ValueError"


def test_identity_classification_read_failure_preserves_actual_error(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(0, 0, 0))
    device.confirm_profile_identity = lambda target: (_ for _ in ()).throw(
        ValueError("profile did not load")
    )
    device.read_profile_observation = lambda: (_ for _ in ()).throw(
        ConnectionError("appium transport lost")
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ConnectionError"


def test_third_identity_mismatch_remains_deferred(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE round_assignments SET attempt_count = 3 WHERE assignment_id = ?",
            (assignment.assignment_id,),
        )
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def mismatch(target) -> None:
        raise ProfileIdentityMismatch("profile mismatch")

    device.confirm_profile_identity = mismatch
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(repository.assignment(assignment.assignment_id))

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ProfileIdentityMismatch"


def test_value_error_after_identity_confirmation_remains_deferred(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE round_assignments SET attempt_count = 3 WHERE assignment_id = ?",
            (assignment.assignment_id,),
        )
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def unreadable_metrics() -> ProfileObservation:
        raise ValueError("profile metrics are incomplete")

    device.read_profile_observation = unreadable_metrics
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(repository.assignment(assignment.assignment_id))

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.visit_confirmed_at_ms == 1_000
    assert stored.last_error_code == "ValueError"


def test_third_device_readiness_value_error_remains_deferred(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE round_assignments SET attempt_count = 3 WHERE assignment_id = ?",
            (assignment.assignment_id,),
        )
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))

    def unavailable_device() -> None:
        raise ValueError("Appium session is unavailable")

    device.ensure_ready = unavailable_device
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
    )

    worker.run_assignment(repository.assignment(assignment.assignment_id))

    stored = repository.assignment(assignment.assignment_id)
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.last_error_code == "ValueError"


def test_ineligible_profile_completes_without_opening_video(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(10, 20, 5))
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    assert (
        repository.assignment(assignment.assignment_id).phase
        is AssignmentPhase.COMPLETED
    )
    assert device.opened_videos == []
    assert device.action_calls == []
    plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    assert plan is not None and plan.effective_outcome is OutcomeKind.TRACE


def test_inaccessible_profile_completes_as_confirmed_trace(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=ProfileAccessState.INACCESSIBLE,
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    worker.run_assignment(assignment)

    stored = repository.assignment(assignment.assignment_id)
    snapshot = repository.profile_snapshot(assignment.round_id, assignment.identity_key)
    plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    assert stored.phase is AssignmentPhase.COMPLETED
    assert snapshot is not None
    assert snapshot.access_state is ProfileAccessState.INACCESSIBLE
    assert snapshot.eligible is False
    assert plan is not None and plan.effective_outcome is OutcomeKind.TRACE
    assert device.opened_videos == []


def test_eligible_trace_opens_video_without_interaction(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(20, 10, 5))
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.TRACE),
    )

    worker.run_assignment(assignment)

    assert (
        repository.assignment(assignment.assignment_id).phase
        is AssignmentPhase.COMPLETED
    )
    assert len(device.opened_videos) == 1
    assert device.action_calls == []


def test_shared_eligible_snapshot_waits_for_second_device_video_grid(
    tmp_path: Path,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    target = Target(
        target_id="user-1",
        username="buyer",
        profile_url="https://www.tiktok.com/@buyer",
        source_video_id="video-source",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = repository.import_pool("comments.csv", "2" * 64, (target,))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
    )
    first = repository.claim_next_assignment(
        round_id, "phone-01", "worker-1", now_ms=1_000
    )
    assert first is not None
    repository.record_visit_confirmed(first.assignment_id, "worker-1", now_ms=1_000)
    assert repository.claim_snapshot_lease(
        round_id,
        first.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=10_000,
    )
    repository.publish_profile_snapshot(
        round_id,
        first.identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=ProfileMetrics(20, 10, 5),
        private_account=False,
        observed_at_ms=1_001,
    )
    repository.complete_assignment(
        first.assignment_id,
        "worker-1",
        AssignmentPhase.IDENTITY_CONFIRMED,
        now_ms=1_002,
    )
    second = repository.claim_next_assignment(
        round_id, "phone-02", "worker-2", now_ms=2_000
    )
    assert second is not None
    device = SharedSnapshotDelayedGridDevice()
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-02",
        owner_id="worker-2",
        clock_ms=lambda: 2_000,
        plan_provider=_forced_plan(OutcomeKind.TRACE),
    )

    worker.run_assignment(second)

    assert (
        repository.assignment(second.assignment_id).phase is AssignmentPhase.COMPLETED
    )
    assert device.observation_reads == 0
    assert device.driver.post_queries == 2


@pytest.mark.parametrize(
    ("local_access_state", "expected_phase", "expected_error"),
    [
        (
            ProfileAccessState.MISSING,
            AssignmentPhase.COMPLETED,
            "profile_missing_after_snapshot",
        ),
        (
            ProfileAccessState.SUSPENDED,
            AssignmentPhase.COMPLETED,
            "profile_suspended_after_snapshot",
        ),
        (
            ProfileAccessState.INACCESSIBLE,
            AssignmentPhase.DEFERRED,
            "RuntimeError",
        ),
    ],
)
def test_shared_eligible_snapshot_only_finishes_for_explicitly_unavailable_profile(
    tmp_path: Path,
    local_access_state: ProfileAccessState,
    expected_phase: AssignmentPhase,
    expected_error: str,
) -> None:
    repository = AcquisitionRepository(tmp_path / "tikpoc.db", clock_ms=lambda: 1_000)
    repository.migrate()
    target = Target(
        target_id="user-1",
        username="buyer",
        profile_url="https://www.tiktok.com/@buyer",
        source_video_id="video-source",
        sec_uid="sec-1",
        identity_key="sec:sec-1",
        source_line_numbers=(2,),
    )
    pool = repository.import_pool("comments.csv", "3" * 64, (target,))
    round_id = create_exposure_round(
        repository,
        pool_id=pool.pool_id,
        device_seeds={"phone-01": "seed-a", "phone-02": "seed-b"},
        starts_at_ms=1_000,
        min_inter_device_gap_ms=0,
    )
    first = repository.claim_next_assignment(
        round_id, "phone-01", "worker-1", now_ms=1_000
    )
    assert first is not None
    repository.record_visit_confirmed(first.assignment_id, "worker-1", now_ms=1_000)
    assert repository.claim_snapshot_lease(
        round_id,
        first.identity_key,
        "phone-01",
        now_ms=1_000,
        ttl_ms=10_000,
    )
    repository.publish_profile_snapshot(
        round_id,
        first.identity_key,
        device_id="phone-01",
        observed_username="buyer",
        metrics=ProfileMetrics(20, 10, 5),
        private_account=False,
        observed_at_ms=1_001,
    )
    repository.complete_assignment(
        first.assignment_id,
        "worker-1",
        AssignmentPhase.IDENTITY_CONFIRMED,
        now_ms=1_002,
    )
    second = repository.claim_next_assignment(
        round_id, "phone-02", "worker-2", now_ms=2_000
    )
    assert second is not None
    device = ScriptedVerifiedDevice(metrics=ProfileMetrics(0, 0, 0), video_keys=())
    device.read_profile_observation = lambda: ProfileObservation(
        observed_username="buyer",
        metrics=None,
        private_account=False,
        access_state=local_access_state,
    )

    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-02",
        owner_id="worker-2",
        clock_ms=lambda: 2_000,
        plan_provider=lambda *args, **kwargs: pytest.fail(
            "missing local profile must not reserve an interaction"
        ),
    )

    worker.run_assignment(second)

    stored = repository.assignment(second.assignment_id)
    assert stored.phase is expected_phase
    assert stored.last_error_code == expected_error
    assert repository.action_plan(round_id, second.identity_key, "phone-02") is None


def test_restart_reconciles_uncertain_plan_without_second_click_or_reservation(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)

    class InterruptedReconciliationDevice(ScriptedVerifiedDevice):
        def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult:
            self.reconcile_calls.append(outcome)
            raise RuntimeError("reconciliation interrupted")

    first_device = InterruptedReconciliationDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.UNCERTAIN],
    )
    first_worker = MobileAssignmentWorker(
        repository,
        first_device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: 1_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
        max_action_attempts=1,
    )
    first_worker.run_assignment(assignment)
    first_plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    assert first_plan is not None and first_plan.state is ActionPlanState.UNCERTAIN
    assert first_device.reconcile_calls == [OutcomeKind.LIKE]

    retry = repository.claim_next_assignment(
        assignment.round_id,
        "phone-01",
        "worker-2",
        now_ms=301_000,
    )
    assert retry is not None
    second_device = ScriptedVerifiedDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.CONFIRMED],
    )
    second_worker = MobileAssignmentWorker(
        repository,
        second_device,
        device_id="phone-01",
        owner_id="worker-2",
        clock_ms=lambda: 301_000,
        plan_provider=_forced_plan(OutcomeKind.LIKE),
    )

    second_worker.run_assignment(retry)

    final_plan = repository.action_plan(
        assignment.round_id, assignment.identity_key, "phone-01"
    )
    quota = repository.quota_window("phone-01", OutcomeKind.LIKE, 0)
    assert final_plan is not None and final_plan.plan_id == first_plan.plan_id
    assert final_plan.state is ActionPlanState.CONFIRMED
    assert second_device.action_calls == []
    assert second_device.reconcile_calls == [OutcomeKind.LIKE]
    assert quota is not None
    assert quota.reserved_count == 1
    assert quota.confirmed_count == 1
    assert quota.uncertain_count == 0


def test_worker_renews_assignment_lease_before_slow_action(tmp_path: Path) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    current_ms = [100_000]
    observed_lease_expiry: list[int] = []

    class LeaseObservingDevice(ScriptedVerifiedDevice):
        def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
            current_ms[0] = 150_000
            observed_lease_expiry.append(
                repository.assignment(assignment.assignment_id).lease_expires_at_ms
            )
            return super().execute_outcome(outcome)

    device = LeaseObservingDevice(
        metrics=ProfileMetrics(20, 10, 5),
        action_results=[ActionResult.CONFIRMED],
    )
    worker = MobileAssignmentWorker(
        repository,
        device,
        device_id="phone-01",
        owner_id="worker-1",
        clock_ms=lambda: current_ms[0],
        plan_provider=_forced_plan(OutcomeKind.LIKE),
        assignment_lease_ttl_ms=180_000,
    )

    worker.run_assignment(assignment)

    assert observed_lease_expiry == [280_000]
    assert (
        repository.assignment(assignment.assignment_id).phase
        is AssignmentPhase.COMPLETED
    )

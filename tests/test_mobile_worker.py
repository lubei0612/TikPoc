from collections import deque
from pathlib import Path

import pytest

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import (
    ActionPlanState,
    ActionResult,
    AssignmentStage,
    AssignmentPhase,
    DeviceDiagnostics,
    OutcomeKind,
    ProfileAccessState,
    ProfileObservation,
)
from tikpoc.importer import Target
from tikpoc.device import ProfileIdentityMismatch
from tikpoc.mobile_worker import MobileAssignmentWorker
from tikpoc.models import ProfileMetrics
from tikpoc.outcome_planner import get_or_create_plan
from tikpoc.rounds import create_exposure_round


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

    def open_target(self, target) -> None:
        super().open_target(target)
        self.clock.advance(100)

    def confirm_profile_identity(self, target) -> None:
        self.clock.advance(200)

    def read_profile_observation(self) -> ProfileObservation:
        self.clock.advance(300)
        return super().read_profile_observation()

    def open_and_confirm_video(self, video_key: str) -> None:
        super().open_and_confirm_video(video_key)
        self.clock.advance(400)

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        self.clock.advance(500)
        return super().execute_outcome(outcome)


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
    ):
        return get_or_create_plan(
            repository,
            round_id,
            identity_key,
            device_id,
            now_ms=now_ms,
            forced_draw=outcome,
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


def test_slow_action_is_deferred_without_false_completion(tmp_path: Path) -> None:
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
    assert stored.phase is AssignmentPhase.DEFERRED
    assert stored.completed_at_ms is None
    assert repository.round_completion(assignment.round_id).completed == 0
    assert device.action_calls == [OutcomeKind.REPOST]
    assert device.reconcile_calls == [OutcomeKind.REPOST, OutcomeKind.REPOST]
    assert device.diagnostic_calls == 1


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


def test_restart_reconciles_uncertain_plan_without_second_click_or_reservation(
    tmp_path: Path,
) -> None:
    repository, assignment = _claimed_assignment(tmp_path)
    first_device = ScriptedVerifiedDevice(
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

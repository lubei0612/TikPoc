from collections import deque
from pathlib import Path

from tikpoc.acquisition_db import AcquisitionRepository
from tikpoc.acquisition_models import (
    ActionPlanState,
    ActionResult,
    AssignmentPhase,
    DeviceDiagnostics,
    OutcomeKind,
    ProfileAccessState,
    ProfileObservation,
)
from tikpoc.importer import Target
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

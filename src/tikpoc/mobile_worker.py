import hashlib
import time
from collections.abc import Callable
from typing import Protocol

from .acquisition_db import AcquisitionRepository, DeviceWorkerLeaseLost
from .acquisition_models import (
    ActionPlan,
    ActionPlanState,
    ActionResult,
    AssignmentPhase,
    AssignmentStage,
    DeviceDiagnostics,
    OutcomeKind,
    PoolTarget,
    ProfileAccessState,
    ProfileObservation,
    RoundAssignment,
)
from .outcome_planner import get_or_create_plan


def _clock_ms() -> int:
    return int(time.time() * 1000)


class VerifiedTikTokDevice(Protocol):
    def ensure_ready(self) -> None: ...

    def open_target(self, target: PoolTarget) -> None: ...

    def confirm_profile_identity(self, target: PoolTarget) -> None: ...

    def read_profile_observation(self) -> ProfileObservation: ...

    def list_video_keys(self) -> tuple[str, ...]: ...

    def open_and_confirm_video(self, video_key: str) -> None: ...

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult: ...

    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult: ...

    def capture_diagnostics(self) -> DeviceDiagnostics: ...

    def recover(self, phase: AssignmentPhase) -> None: ...


class PlanProvider(Protocol):
    def __call__(
        self,
        repository: AcquisitionRepository,
        round_id: str,
        identity_key: str,
        device_id: str,
        now_ms: int,
        **worker_fence: object,
    ) -> ActionPlan: ...


MAX_PROFILE_OPEN_ATTEMPTS = 1


class ProfileUnreachable(ValueError):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def _default_plan_provider(
    repository: AcquisitionRepository,
    round_id: str,
    identity_key: str,
    device_id: str,
    now_ms: int,
    **worker_fence: object,
) -> ActionPlan:
    return get_or_create_plan(
        repository,
        round_id,
        identity_key,
        device_id,
        now_ms=now_ms,
        **worker_fence,
    )


class MobileAssignmentWorker:
    def __init__(
        self,
        repository: AcquisitionRepository,
        device: VerifiedTikTokDevice,
        *,
        device_id: str,
        owner_id: str,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
        clock_ms: Callable[[], int] = _clock_ms,
        plan_provider: PlanProvider = _default_plan_provider,
        snapshot_lease_ttl_ms: int = 30_000,
        assignment_lease_ttl_ms: int = 180_000,
        max_action_attempts: int = 3,
        action_timeout_ms: int = 90_000,
        retry_delay_ms: int = 300_000,
    ) -> None:
        if (
            max_action_attempts < 1
            or action_timeout_ms < 1
            or assignment_lease_ttl_ms <= action_timeout_ms
            or retry_delay_ms < 0
        ):
            raise ValueError("mobile recovery limits are invalid")
        if (worker_account_id is None) != (worker_fence_token is None):
            raise ValueError("mobile worker fence identity is incomplete")
        self.repository = repository
        self.device = device
        self.device_id = device_id
        self.owner_id = owner_id
        self.worker_account_id = worker_account_id
        self.worker_fence_token = worker_fence_token
        self.clock_ms = clock_ms
        self.plan_provider = plan_provider
        self.snapshot_lease_ttl_ms = snapshot_lease_ttl_ms
        self.assignment_lease_ttl_ms = assignment_lease_ttl_ms
        self.max_action_attempts = max_action_attempts
        self.action_timeout_ms = action_timeout_ms
        self.retry_delay_ms = retry_delay_ms

    def run_assignment(self, assignment: RoundAssignment) -> None:
        if assignment.device_id != self.device_id:
            raise ValueError("assignment belongs to a different device")
        current = self.repository.assignment(assignment.assignment_id)
        if current.lease_owner != self.owner_id:
            raise ValueError("worker does not hold the assignment lease")
        if current.phase is not AssignmentPhase.PROFILE_OPENING:
            raise ValueError("assignment must be claimed before execution")
        self._renew_lease(current.assignment_id)
        try:
            self._run_claimed(current)
        except DeviceWorkerLeaseLost:
            raise
        except Exception as error:
            stored = self.repository.assignment(current.assignment_id)
            if (
                type(error) is ProfileUnreachable
                and stored.phase is AssignmentPhase.PROFILE_OPENING
                and stored.visit_confirmed_at_ms is None
                and stored.attempt_count >= MAX_PROFILE_OPEN_ATTEMPTS
            ):
                self._skip_unreachable(current.assignment_id, error)
            elif (
                type(error) is ProfileUnreachable
                and stored.phase is AssignmentPhase.PROFILE_OPENING
                and stored.visit_confirmed_at_ms is not None
            ):
                self.repository.transition_assignment(
                    current.assignment_id,
                    self.owner_id,
                    AssignmentPhase.PROFILE_OPENING,
                    AssignmentPhase.IDENTITY_CONFIRMED,
                    now_ms=self.clock_ms(),
                    details={
                        "reason": "confirmed_visit_profile_unreachable",
                        "failure_stage": error.stage,
                    },
                    **self._assignment_fence_kwargs(),
                )
                self.repository.complete_assignment(
                    current.assignment_id,
                    self.owner_id,
                    AssignmentPhase.IDENTITY_CONFIRMED,
                    now_ms=self.clock_ms(),
                    terminal_error_code="confirmed_visit_profile_unreachable",
                    completion_details={
                        "reason": "confirmed_visit_profile_unreachable",
                        "failure_stage": error.stage,
                    },
                    **self._assignment_fence_kwargs(),
                )
            else:
                self._defer(
                    current.assignment_id,
                    type(error).__name__,
                    manual_retry_only=(
                        type(error) is ProfileUnreachable
                        and stored.visit_confirmed_at_ms is not None
                    ),
                )

    def _run_claimed(self, assignment: RoundAssignment) -> None:
        target = PoolTarget(
            pool_id=assignment.pool_id,
            identity_key=assignment.identity_key,
            target_id=assignment.target_id,
            sec_uid=assignment.sec_uid,
            username=assignment.username,
            profile_url=assignment.profile_url,
            source_video_id=assignment.source_video_id,
            source_line_numbers=(),
            ordinal=0,
        )
        route_started_at_ms = self.clock_ms()
        self.device.ensure_ready()
        self.device.open_target(target)
        self._record_stage(
            assignment.assignment_id, AssignmentStage.ROUTE, route_started_at_ms
        )
        identity_started_at_ms = self.clock_ms()
        try:
            self.device.confirm_profile_identity(target)
        except ValueError as error:
            if type(error) is ValueError:
                try:
                    failed_observation = self.device.read_profile_observation()
                except Exception as observation_error:
                    raise observation_error from error
                expected_username = target.username.strip().removeprefix("@").casefold()
                observed_username = (
                    failed_observation.observed_username.strip()
                    .removeprefix("@")
                    .casefold()
                )
                if observed_username == expected_username and (
                    failed_observation.access_state
                    in {
                        ProfileAccessState.MISSING,
                        ProfileAccessState.SUSPENDED,
                    }
                ):
                    raise ProfileUnreachable("identity", str(error)) from error
            raise
        self._record_stage(
            assignment.assignment_id, AssignmentStage.IDENTITY, identity_started_at_ms
        )
        now_ms = self.clock_ms()
        self.repository.record_visit_confirmed(
            assignment.assignment_id,
            self.owner_id,
            now_ms=now_ms,
            **self._assignment_fence_kwargs(),
        )
        self._renew_lease(assignment.assignment_id)

        metrics_started_at_ms = self.clock_ms()
        snapshot = self.repository.profile_snapshot(
            assignment.round_id, assignment.identity_key
        )
        if snapshot is None:
            owns_snapshot = self.repository.claim_snapshot_lease(
                assignment.round_id,
                assignment.identity_key,
                self.device_id,
                now_ms=now_ms,
                ttl_ms=self.snapshot_lease_ttl_ms,
                **self._action_fence_kwargs(),
            )
            if not owns_snapshot:
                self.repository.transition_assignment(
                    assignment.assignment_id,
                    self.owner_id,
                    AssignmentPhase.IDENTITY_CONFIRMED,
                    AssignmentPhase.WAITING_SNAPSHOT,
                    now_ms=now_ms,
                    **self._assignment_fence_kwargs(),
                )
                self._defer(assignment.assignment_id, "snapshot_pending")
                return
            observation = self.device.read_profile_observation()
            snapshot = self.repository.publish_profile_snapshot(
                assignment.round_id,
                assignment.identity_key,
                device_id=self.device_id,
                observed_username=observation.observed_username,
                metrics=observation.metrics,
                private_account=observation.private_account,
                access_state=observation.access_state,
                observed_at_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
        self._record_stage(
            assignment.assignment_id, AssignmentStage.METRICS, metrics_started_at_ms
        )

        visible_video_keys: tuple[str, ...] = ()
        existing_plan = self.repository.action_plan(
            assignment.round_id, assignment.identity_key, self.device_id
        )
        if snapshot.eligible and (
            existing_plan is None or existing_plan.video_key is None
        ):
            visible_video_keys = self.device.list_video_keys()
            if not visible_video_keys:
                current_observation = self.device.read_profile_observation()
                if current_observation.access_state in {
                    ProfileAccessState.MISSING,
                    ProfileAccessState.SUSPENDED,
                }:
                    terminal_code = f"profile_{current_observation.access_state.value}_after_snapshot"
                    self.repository.complete_assignment(
                        assignment.assignment_id,
                        self.owner_id,
                        AssignmentPhase.IDENTITY_CONFIRMED,
                        now_ms=self.clock_ms(),
                        terminal_error_code=terminal_code,
                        completion_details={
                            "reason": terminal_code,
                            "snapshot_access_state": snapshot.access_state.value,
                            "local_access_state": (
                                current_observation.access_state.value
                            ),
                        },
                        **self._assignment_fence_kwargs(),
                    )
                    return
                raise RuntimeError("eligible profile has no visible video")

        plan_args = (
            self.repository,
            assignment.round_id,
            assignment.identity_key,
            self.device_id,
            self.clock_ms(),
        )
        if self.worker_fence_token is None:
            plan = self.plan_provider(*plan_args)
        else:
            plan = self.plan_provider(*plan_args, **self._action_fence_kwargs())
        if plan.state is ActionPlanState.CONFIRMED:
            self.repository.complete_assignment(
                assignment.assignment_id,
                self.owner_id,
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            return
        if not snapshot.eligible:
            self.repository.confirm_trace_plan(
                plan.plan_id,
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
            self.repository.complete_assignment(
                assignment.assignment_id,
                self.owner_id,
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            return

        video_started_at_ms = self.clock_ms()
        if plan.video_key is None:
            plan = self.repository.set_plan_video(
                plan.plan_id,
                self._select_video(plan.seed, visible_video_keys),
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
        self.repository.transition_assignment(
            assignment.assignment_id,
            self.owner_id,
            AssignmentPhase.IDENTITY_CONFIRMED,
            AssignmentPhase.VIDEO_OPENING,
            now_ms=self.clock_ms(),
            **self._assignment_fence_kwargs(),
        )
        self._renew_lease(assignment.assignment_id)
        self.device.open_and_confirm_video(plan.video_key or "")
        self._record_stage(
            assignment.assignment_id, AssignmentStage.VIDEO, video_started_at_ms
        )
        self.repository.transition_assignment(
            assignment.assignment_id,
            self.owner_id,
            AssignmentPhase.VIDEO_OPENING,
            AssignmentPhase.VIDEO_CONFIRMED,
            now_ms=self.clock_ms(),
            **self._assignment_fence_kwargs(),
        )
        if plan.effective_outcome is OutcomeKind.TRACE:
            action_started_at_ms = self.clock_ms()
            self.repository.confirm_trace_plan(
                plan.plan_id,
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
            self._record_stage(
                assignment.assignment_id,
                AssignmentStage.ACTION,
                action_started_at_ms,
            )
            self.repository.complete_assignment(
                assignment.assignment_id,
                self.owner_id,
                AssignmentPhase.VIDEO_CONFIRMED,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            return
        self._execute_interaction(assignment.assignment_id, plan)

    def _execute_interaction(self, assignment_id: int, plan: ActionPlan) -> None:
        started_at_ms = self.clock_ms()
        attempts = 0
        reconciliation_attempts = 0
        self._renew_lease(assignment_id)
        if plan.state in {ActionPlanState.EXECUTING, ActionPlanState.UNCERTAIN}:
            phase = AssignmentPhase.ACTION_RECONCILING
            self.repository.transition_assignment(
                assignment_id,
                self.owner_id,
                AssignmentPhase.VIDEO_CONFIRMED,
                phase,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            result = self.device.reconcile_outcome(plan.effective_outcome)
            reconciliation_attempts = 1
        else:
            self.repository.transition_assignment(
                assignment_id,
                self.owner_id,
                AssignmentPhase.VIDEO_CONFIRMED,
                AssignmentPhase.QUOTA_RESERVED,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            self.repository.transition_assignment(
                assignment_id,
                self.owner_id,
                AssignmentPhase.QUOTA_RESERVED,
                AssignmentPhase.ACTION_EXECUTING,
                now_ms=self.clock_ms(),
                **self._assignment_fence_kwargs(),
            )
            phase = AssignmentPhase.ACTION_EXECUTING
            self.repository.mark_action_executing(
                plan.plan_id,
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
            result = self.device.execute_outcome(plan.effective_outcome)

        while True:
            attempts += 1
            if phase is AssignmentPhase.ACTION_RECONCILING and (
                result is ActionResult.NOT_APPLIED
                or (
                    result is ActionResult.UNAVAILABLE
                    and plan.effective_outcome is not OutcomeKind.REPOST
                )
            ):
                result = ActionResult.UNCERTAIN
            stored_plan = self.repository.record_action_result(
                plan.plan_id,
                result,
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
            if result is ActionResult.CONFIRMED:
                self._record_stage(assignment_id, AssignmentStage.ACTION, started_at_ms)
                self.repository.complete_assignment(
                    assignment_id,
                    self.owner_id,
                    phase,
                    now_ms=self.clock_ms(),
                    **self._assignment_fence_kwargs(),
                )
                return
            if result is ActionResult.UNAVAILABLE:
                if plan.effective_outcome is not OutcomeKind.REPOST:
                    self._record_stage(
                        assignment_id, AssignmentStage.ACTION, started_at_ms
                    )
                    self._defer(assignment_id, "unexpected_action_unavailable")
                    return
                self.repository.confirm_action_unavailable_as_trace(
                    plan.plan_id,
                    now_ms=self.clock_ms(),
                    **self._action_fence_kwargs(),
                )
                self._record_stage(assignment_id, AssignmentStage.ACTION, started_at_ms)
                self.repository.complete_assignment(
                    assignment_id,
                    self.owner_id,
                    phase,
                    now_ms=self.clock_ms(),
                    **self._assignment_fence_kwargs(),
                )
                return
            if result is ActionResult.UNCERTAIN:
                if reconciliation_attempts >= 1:
                    self._record_stage(
                        assignment_id, AssignmentStage.ACTION, started_at_ms
                    )
                    self.repository.complete_assignment(
                        assignment_id,
                        self.owner_id,
                        phase,
                        now_ms=self.clock_ms(),
                        terminal_error_code="action_uncertain_terminal",
                        completion_details={
                            "reason": "action_uncertain_after_single_reconciliation"
                        },
                        **self._assignment_fence_kwargs(),
                    )
                    return
                if phase is AssignmentPhase.ACTION_EXECUTING:
                    self.repository.transition_assignment(
                        assignment_id,
                        self.owner_id,
                        phase,
                        AssignmentPhase.ACTION_RECONCILING,
                        now_ms=self.clock_ms(),
                        **self._assignment_fence_kwargs(),
                    )
                    phase = AssignmentPhase.ACTION_RECONCILING
                self.device.recover(phase)
                self._renew_lease(assignment_id)
                reconciliation_attempts += 1
                result = self.device.reconcile_outcome(plan.effective_outcome)
                continue

            timed_out = self.clock_ms() - started_at_ms >= self.action_timeout_ms
            if attempts >= self.max_action_attempts or timed_out:
                self._record_stage(assignment_id, AssignmentStage.ACTION, started_at_ms)
                self._defer(assignment_id, f"action_{result.value}")
                return

            self.device.recover(phase)
            if phase is AssignmentPhase.ACTION_RECONCILING:
                self.repository.transition_assignment(
                    assignment_id,
                    self.owner_id,
                    phase,
                    AssignmentPhase.ACTION_EXECUTING,
                    now_ms=self.clock_ms(),
                    **self._assignment_fence_kwargs(),
                )
                phase = AssignmentPhase.ACTION_EXECUTING
            if stored_plan.state is not ActionPlanState.PLANNED:
                raise RuntimeError("not-applied result did not reset action plan")
            self.repository.mark_action_executing(
                plan.plan_id,
                now_ms=self.clock_ms(),
                **self._action_fence_kwargs(),
            )
            self._renew_lease(assignment_id)
            result = self.device.execute_outcome(plan.effective_outcome)

    def _defer(
        self,
        assignment_id: int,
        error_code: str,
        *,
        manual_retry_only: bool = False,
    ) -> None:
        diagnostics = self._capture_diagnostics()
        self.repository.defer_assignment(
            assignment_id,
            self.owner_id,
            now_ms=self.clock_ms(),
            retry_delay_ms=self.retry_delay_ms,
            error_code=error_code,
            diagnostics=diagnostics,
            manual_retry_only=manual_retry_only,
            **self._assignment_fence_kwargs(),
        )

    def _skip_unreachable(self, assignment_id: int, error: ProfileUnreachable) -> None:
        original_error = error.__cause__ or error
        self.repository.skip_unreachable_assignment(
            assignment_id,
            self.owner_id,
            now_ms=self.clock_ms(),
            error_code="profile_unreachable",
            original_error_code=type(original_error).__name__,
            failure_stage=error.stage,
            diagnostics=self._capture_diagnostics(),
            **self._assignment_fence_kwargs(),
        )

    def _assignment_fence_kwargs(self) -> dict[str, object]:
        return {
            "worker_account_id": self.worker_account_id,
            "worker_fence_token": self.worker_fence_token,
        }

    def _action_fence_kwargs(self) -> dict[str, object]:
        return {
            "worker_owner_id": self.owner_id
            if self.worker_fence_token is not None
            else None,
            **self._assignment_fence_kwargs(),
        }

    def _capture_diagnostics(self) -> DeviceDiagnostics:
        try:
            return self.device.capture_diagnostics()
        except Exception:
            return DeviceDiagnostics(ui_summary="diagnostic capture failed")

    def _renew_lease(self, assignment_id: int) -> None:
        now_ms = self.clock_ms()
        self.repository.renew_assignment_lease(
            assignment_id,
            self.owner_id,
            now_ms=now_ms,
            ttl_ms=self.assignment_lease_ttl_ms,
            **self._assignment_fence_kwargs(),
        )

    def _record_stage(
        self,
        assignment_id: int,
        stage: AssignmentStage,
        started_at_ms: int,
    ) -> None:
        recorded_at_ms = self.clock_ms()
        self.repository.record_assignment_stage_timing(
            assignment_id,
            stage,
            duration_ms=recorded_at_ms - started_at_ms,
            recorded_at_ms=recorded_at_ms,
            **self._action_fence_kwargs(),
        )

    @staticmethod
    def _select_video(seed: str, video_keys: tuple[str, ...]) -> str:
        index_seed = hashlib.sha256(f"{seed}\0video".encode()).digest()
        return video_keys[int.from_bytes(index_seed[:8], "big") % len(video_keys)]

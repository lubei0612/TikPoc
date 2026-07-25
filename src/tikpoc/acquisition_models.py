from dataclasses import dataclass
from enum import StrEnum

from .models import ProfileMetrics


class RoundState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


class PriorityBatchState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BARRIER = "barrier"
    COMPLETED = "completed"


class PriorityBatchClass(StrEnum):
    BACKGROUND = "background"
    LIVE_INTERRUPT = "live_interrupt"


class AssignmentPhase(StrEnum):
    PENDING = "pending"
    PROFILE_OPENING = "profile_opening"
    IDENTITY_CONFIRMED = "identity_confirmed"
    WAITING_SNAPSHOT = "waiting_snapshot"
    VIDEO_OPENING = "video_opening"
    VIDEO_CONFIRMED = "video_confirmed"
    QUOTA_RESERVED = "quota_reserved"
    ACTION_EXECUTING = "action_executing"
    ACTION_RECONCILING = "action_reconciling"
    DEFERRED = "deferred"
    SKIPPED = "skipped"
    COMPLETED = "completed"


class AssignmentStage(StrEnum):
    ROUTE = "route"
    IDENTITY = "identity"
    METRICS = "metrics"
    VIDEO = "video"
    ACTION = "action"


class ProfileAccessState(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SUSPENDED = "suspended"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


class OutcomeKind(StrEnum):
    LIKE = "like"
    FAVORITE = "favorite"
    REPOST = "repost"
    TRACE = "trace"


class ActionPlanState(StrEnum):
    PLANNED = "planned"
    EXECUTING = "executing"
    CONFIRMED = "confirmed"
    UNCERTAIN = "uncertain"


class ActionResult(StrEnum):
    CONFIRMED = "confirmed"
    NOT_APPLIED = "not_applied"
    UNCERTAIN = "uncertain"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PoolImport:
    pool_id: str
    unique_targets: int
    source_rows: int


@dataclass(frozen=True)
class PoolTarget:
    pool_id: str
    identity_key: str
    target_id: str
    sec_uid: str
    username: str
    profile_url: str
    source_video_id: str
    source_line_numbers: tuple[int, ...]
    ordinal: int


@dataclass(frozen=True)
class ExposureRound:
    round_id: str
    pool_id: str
    state: RoundState
    starts_at_ms: int
    min_inter_device_gap_ms: int
    min_repeat_gap_ms: int
    created_at_ms: int


@dataclass(frozen=True)
class PriorityBatch:
    batch_id: str
    parent_round_id: str
    priority_round_id: str
    pool_id: str
    source_live_id: str
    source_checksum: str
    batch_class: PriorityBatchClass
    queue_sequence: int
    state: PriorityBatchState
    created_at_ms: int
    completed_at_ms: int | None


@dataclass(frozen=True)
class RoundAssignment:
    assignment_id: int
    round_id: str
    pool_id: str
    identity_key: str
    target_id: str
    sec_uid: str
    username: str
    profile_url: str
    source_video_id: str
    device_id: str
    order_key: str
    phase: AssignmentPhase
    attempt_count: int
    next_attempt_at_ms: int
    visit_confirmed_at_ms: int | None
    completed_at_ms: int | None
    last_error_code: str | None
    lease_owner: str | None
    lease_expires_at_ms: int


@dataclass(frozen=True)
class ProfileSnapshot:
    round_id: str
    identity_key: str
    observed_by_device_id: str
    observed_username: str
    metrics: ProfileMetrics | None
    private_account: bool
    access_state: ProfileAccessState
    eligible: bool
    reason: str
    observed_at_ms: int


@dataclass(frozen=True)
class ActionPlan:
    plan_id: int
    round_id: str
    identity_key: str
    device_id: str
    seed: str
    requested_outcome: OutcomeKind
    effective_outcome: OutcomeKind
    quota_window_start_ms: int | None
    quota_reason: str | None
    video_key: str | None
    state: ActionPlanState
    created_at_ms: int


@dataclass(frozen=True)
class QuotaWindow:
    device_id: str
    outcome: OutcomeKind
    window_start_ms: int
    reserved_count: int
    confirmed_count: int
    uncertain_count: int


@dataclass(frozen=True)
class ActionPacingState:
    device_id: str
    outcome: OutcomeKind
    tokens: float
    updated_at_ms: int
    next_due_at_ms: int
    rolling_used: int
    limit: int
    ready: bool


@dataclass(frozen=True)
class ProfileObservation:
    observed_username: str
    metrics: ProfileMetrics | None
    private_account: bool
    access_state: ProfileAccessState


@dataclass(frozen=True)
class DeviceDiagnostics:
    screenshot_path: str = ""
    ui_summary: str = ""


@dataclass(frozen=True)
class RoundCompletion:
    total: int
    visits_confirmed: int
    completed: int
    deferred: int
    skipped: int = 0


@dataclass(frozen=True)
class AssignmentTransition:
    history_id: int
    assignment_id: int
    from_phase: AssignmentPhase
    to_phase: AssignmentPhase
    details: dict[str, object]
    changed_at_ms: int


@dataclass(frozen=True)
class AssignmentStageTiming:
    assignment_id: int
    stage: AssignmentStage
    duration_ms: int
    recorded_at_ms: int


@dataclass(frozen=True)
class AssignmentCommandMetrics:
    assignment_id: int
    stage: AssignmentStage
    command_count: int
    command_duration_ms: int
    page_source_reads: int
    element_queries: int
    execute_script_calls: int
    recorded_at_ms: int

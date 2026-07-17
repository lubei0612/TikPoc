from dataclasses import dataclass
from enum import StrEnum

from .models import ProfileMetrics


class RoundState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"


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
    COMPLETED = "completed"


class ProfileAccessState(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    SUSPENDED = "suspended"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"


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

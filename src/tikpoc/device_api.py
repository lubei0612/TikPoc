from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSession:
    device_id: str
    account_id: str
    session_epoch: int
    access_token: str = ""


@dataclass(frozen=True)
class MobileTaskEnvelope:
    task_id: str
    assignment_id: int
    round_id: str
    device_id: str
    account_id: str
    session_epoch: int
    lease_id: str
    lease_expires_at_ms: int
    phase: str
    target_id: str
    username: str
    profile_url: str
    plan_id: int = 0
    action: str = ""
    video_key: str = ""


@dataclass(frozen=True)
class MobileTaskResult:
    device_id: str
    session_epoch: int
    task_id: str
    lease_id: str
    idempotency_key: str
    state: str
    phase: str
    evidence: Mapping[str, object]

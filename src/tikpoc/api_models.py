from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


BoundedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class BrowserIdentityRequest(ApiRequest):
    account_id: Identifier
    device_id: Identifier


class BrowserEventRequest(BrowserIdentityRequest):
    event_type: Literal[
        "new_follower",
        "followback_completed",
        "followback_unresolved",
        "browser_dm_received",
    ]
    dedup_key: Identifier
    payload: dict[str, object] = Field(default_factory=dict)


class BrowserReplyPlanRequest(BrowserIdentityRequest):
    conversation_id: Identifier
    fingerprint: Identifier
    participant_username: Identifier
    text: BoundedText
    timestamp_ms: int = Field(ge=0)


class BrowserReplyResultRequest(BrowserIdentityRequest):
    plan_id: int = Field(gt=0)
    state: Identifier


class BrowserActionClaimRequest(BrowserIdentityRequest):
    action_type: Identifier
    action_key: Identifier
    owner_id: Identifier
    timestamp_ms: int = Field(ge=0)
    lease_seconds: int = Field(default=30, ge=1, le=3600)


class BrowserActionResultRequest(BrowserIdentityRequest):
    action_type: Identifier
    action_key: Identifier
    owner_id: Identifier
    state: Identifier


class BrowserHealthRequest(BrowserIdentityRequest):
    page_role: Literal["activity", "messages"]
    path: BoundedText
    signed_in: bool
    timestamp_ms: int = Field(ge=0)


class DeviceEventRequest(ApiRequest):
    device_id: Identifier
    event_type: Identifier
    dedup_key: Identifier
    payload: dict[str, object] = Field(default_factory=dict)


CommandId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]


class OperatorCommand(ApiRequest):
    command_id: CommandId
    scope: Literal["fleet", "round", "device", "assignment"]
    scope_id: Identifier


class RetryCommand(ApiRequest):
    command_id: CommandId
    assignment_id: int = Field(gt=0)


class PoolImportRequest(ApiRequest):
    local_path: BoundedText


class RoundCreateRequest(ApiRequest):
    pool_id: Identifier
    device_seeds: dict[Identifier, Identifier] = Field(min_length=1, max_length=100)
    starts_at_ms: int = Field(ge=0)
    min_inter_device_gap_ms: int = Field(default=900_000, ge=0)
    min_repeat_gap_ms: int = Field(default=72_000_000, ge=0)

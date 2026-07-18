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

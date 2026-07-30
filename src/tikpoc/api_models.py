from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BoundedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
Identifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
VisibleUsername = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=200),
]


class ApiRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class BrowserIdentityRequest(ApiRequest):
    account_id: Identifier
    device_id: Identifier
    observed_username: VisibleUsername = ""
    binding_state: Literal[
        "ready",
        "unverified",
        "mismatch",
        "signed_out",
        "verification_required",
    ] = "unverified"


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


class BrowserWelcomeResultRequest(BrowserIdentityRequest):
    plan_id: int = Field(gt=0)
    state: Literal["sent", "uncertain", "superseded"]


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
    reason: Annotated[str, StringConstraints(max_length=100)] = ""


class BrowserHealthRequest(BrowserIdentityRequest):
    page_role: Literal["activity", "messages"]
    path: BoundedText
    signed_in: bool
    timestamp_ms: int = Field(ge=0)
    last_scan_at_ms: int = Field(default=0, ge=0)
    last_success_at_ms: int = Field(default=0, ge=0)
    scan_state: BoundedText = "not_started"

    @model_validator(mode="after")
    def validate_scan_timestamps(self) -> "BrowserHealthRequest":
        if max(self.last_scan_at_ms, self.last_success_at_ms) > self.timestamp_ms:
            raise ValueError("browser scan timestamps are inconsistent")
        if self.last_success_at_ms and not self.last_scan_at_ms:
            raise ValueError("browser scan timestamps are inconsistent")
        return self


class DeviceEventRequest(ApiRequest):
    device_id: Identifier
    event_type: Identifier
    dedup_key: Identifier
    payload: dict[str, object] = Field(default_factory=dict)


class MobileRegisterRequest(ApiRequest):
    device_id: Identifier
    account_id: Identifier


class MobileHeartbeatRequest(ApiRequest):
    device_id: Identifier
    session_epoch: int = Field(gt=0)
    app_version: Identifier
    phase: Literal[
        "idle",
        "pending",
        "profile_opening",
        "identity_confirmed",
        "waiting_snapshot",
        "video_opening",
        "video_confirmed",
        "quota_reserved",
        "action_executing",
        "action_reconciling",
        "video_verified",
        "comment_submitting",
        "comment_reconciling",
        "verification_required",
        "stable_home",
        "completed",
        "deferred",
        "skipped",
    ]
    queue_depth: int = Field(ge=0, le=50)
    client_timestamp_ms: int = Field(ge=0)


class MobilePullRequest(ApiRequest):
    device_id: Identifier
    session_epoch: int = Field(gt=0)
    round_id: VisibleUsername = ""
    task_kind: Literal["touch", "brand_comment", "hybrid"] = "touch"
    limit: int = Field(default=20, ge=1, le=50)


class LiveBatchTargetRequest(ApiRequest):
    username: Identifier
    sec_uid: VisibleUsername = ""
    uid: VisibleUsername = ""
    source_video_id: VisibleUsername = ""
    collected_at_ms: int = Field(default=0, ge=0)


class LiveBatchRequest(ApiRequest):
    host_round_id: Identifier
    source_live_id: Identifier
    navigation_mode: Literal["deeplink", "search"] = "deeplink"
    targets: list[LiveBatchTargetRequest] = Field(min_length=1, max_length=10_000)


class MobileResultRequest(ApiRequest):
    device_id: Identifier
    session_epoch: int = Field(gt=0)
    task_id: Identifier
    lease_id: Identifier
    idempotency_key: Identifier
    state: Literal["completed", "deferred", "uncertain", "skipped"]
    phase: Identifier
    evidence: dict[str, object] = Field(default_factory=dict)


class CommentVideoRequest(ApiRequest):
    source_url: BoundedText
    creator_username: VisibleUsername = ""
    caption_anchor: VisibleUsername = ""
    command_id: Identifier = "video-add"


class CommentEvidenceItem(ApiRequest):
    cid: Identifier
    text: BoundedText
    digg_count: int = Field(ge=0)
    reply_comment_total: int = Field(ge=0)
    create_time: int = Field(ge=0)
    language: Identifier


class CommentEvidenceRequest(ApiRequest):
    comments: list[CommentEvidenceItem] = Field(min_length=1, max_length=10_000)
    command_id: Identifier = "evidence-import"


class CommentPlanRequest(ApiRequest):
    video_id: Identifier
    persona_id: Identifier
    account_id: Identifier
    display_name: BoundedText
    english: BoundedText
    chinese: BoundedText
    emoji_count: int = Field(ge=0, le=2)
    command_id: Identifier


class CommentPlanApprovalRequest(ApiRequest):
    account_id: Identifier
    command_id: Identifier


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


class LeadCommand(ApiRequest):
    command_id: CommandId


class LeadTakeoverCommand(LeadCommand):
    reason: BoundedText


class ManualReplyPlanCommand(LeadCommand):
    inbound_fingerprint: Identifier
    reply_text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]


class LeadSaleCommand(LeadCommand):
    amount_minor: int = Field(gt=0)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    status: Literal["pending", "confirmed", "refunded", "cancelled"]
    occurred_at_ms: int = Field(ge=0)


class AccountEnableCommand(LeadCommand):
    enabled: bool


class FollowbackCooldownCommand(LeadCommand):
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    cooldown_seconds: int = Field(default=86_400, ge=60, le=604_800)


class ProviderSettingsCommand(ApiRequest):
    base_url: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ]
    api_key: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=8_000)
    ] = ""
    model: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    clear_key: bool = False


class AccountAutomationSettingsCommand(ApiRequest):
    whatsapp: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    telegram: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    offer_context: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=3_000)
    ] = ""
    faq_context: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=3_000)
    ] = ""
    reply_tone: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=500)
    ] = ""
    brand_name: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=200)
    ] = ""
    welcome_after_followback: bool = False
    welcome_language: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=2,
            max_length=50,
            pattern=r"^[A-Za-z][A-Za-z -]*$",
        ),
    ] = "English"

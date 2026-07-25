from collections.abc import Mapping
from dataclasses import dataclass

from .acquisition_models import AssignmentPhase

PROTOCOL_VERSION = 1
SUPPORTED_HELPER_VERSIONS = frozenset({"1.0.0"})
TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
SUPPORTED_COMMANDS = frozenset(
    {
        "health",
        "open_profile",
        "observe_profile",
        "open_video",
        "observe_action",
        "apply_action",
        "diagnostics",
    }
)


class DeviceSideProtocolError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CommandContext:
    command_id: str
    device_id: str
    account_id: str
    fence_token: int
    assignment_id: int
    phase: AssignmentPhase
    deadline_monotonic_ms: int

    def __post_init__(self) -> None:
        if (
            not self.command_id.strip()
            or not self.device_id.strip()
            or not self.account_id.strip()
            or self.fence_token <= 0
            or self.assignment_id <= 0
            or self.deadline_monotonic_ms <= 0
        ):
            raise DeviceSideProtocolError("invalid_command_context")


@dataclass(frozen=True)
class HelperHealth:
    service_enabled: bool
    tiktok_foreground: bool
    surface: str
    busy: bool


@dataclass(frozen=True)
class ProfileEvidence:
    access_state: str
    username: str
    following: int
    followers: int
    video_count: int
    post_handles: tuple[str, ...]
    following_resource_id: str
    followers_resource_id: str


@dataclass(frozen=True)
class VideoEvidence:
    video_key: str
    post_resource_id: str
    video_controls_visible: bool


@dataclass(frozen=True)
class ActionEvidence:
    action: str
    before: str | None = None
    after: str | None = None
    control_resource_id: str = ""
    state: str | None = None


@dataclass(frozen=True)
class HelperDiagnostics:
    node_count: int
    tree_age_ms: int
    visible_nodes: int


HelperEvidence = (
    HelperHealth | ProfileEvidence | VideoEvidence | ActionEvidence | HelperDiagnostics
)


@dataclass(frozen=True)
class HelperResponse:
    status: str
    helper_version: str
    command_id: str
    elapsed_ms: int
    package_name: str
    activity_name: str
    event_sequence: int
    evidence_digest: str
    evidence: HelperEvidence | Mapping[str, object] | None
    error_code: str | None = None


def build_request(
    context: CommandContext, *, command: str, arguments: Mapping[str, object]
) -> dict[str, object]:
    if command not in SUPPORTED_COMMANDS:
        raise DeviceSideProtocolError("unsupported_command")
    return {
        "version": PROTOCOL_VERSION,
        "command_id": context.command_id,
        "command": command,
        "device_id": context.device_id,
        "account_id": context.account_id,
        "fence_token": context.fence_token,
        "assignment_id": context.assignment_id,
        "phase": context.phase.value,
        "deadline_elapsed_ms": context.deadline_monotonic_ms,
        "arguments": dict(arguments),
    }


def parse_response(
    payload: Mapping[str, object],
    *,
    context: CommandContext,
    command: str,
    now_monotonic_ms: int,
    expected_username: str | None = None,
) -> HelperResponse:
    _equal(payload, "version", PROTOCOL_VERSION, "unsupported_protocol_version")
    helper_version = _string(payload, "helper_version")
    if helper_version not in SUPPORTED_HELPER_VERSIONS:
        raise DeviceSideProtocolError("unsupported_helper_version")
    _equal(payload, "command_id", context.command_id, "command_id_mismatch")
    _equal(payload, "device_id", context.device_id, "device_id_mismatch")
    _equal(payload, "account_id", context.account_id, "account_id_mismatch")
    _equal(payload, "fence_token", context.fence_token, "fence_token_mismatch")
    _equal(payload, "assignment_id", context.assignment_id, "assignment_id_mismatch")
    _equal(payload, "phase", context.phase.value, "phase_mismatch")
    if now_monotonic_ms > context.deadline_monotonic_ms:
        raise DeviceSideProtocolError("deadline_expired")
    package_name = _string(payload, "package_name")
    if package_name != TIKTOK_PACKAGE:
        raise DeviceSideProtocolError("unexpected_package")
    status = _string(payload, "status")
    if status not in {"ok", "uncertain", "error"}:
        raise DeviceSideProtocolError("invalid_status")
    elapsed_ms = _nonnegative_int(payload, "elapsed_ms")
    activity_name = _string(payload, "activity_name", allow_empty=True)
    event_sequence = _nonnegative_int(payload, "event_sequence")
    digest = _string(payload, "evidence_digest")
    evidence: HelperEvidence | Mapping[str, object] | None
    error_code = None
    if status == "error":
        error = _mapping(payload, "error")
        error_code = _string(error, "code")
        evidence = None
    else:
        values = _mapping(payload, "evidence")
        evidence = _parse_evidence(command, values, expected_username=expected_username)
    return HelperResponse(
        status=status,
        helper_version=helper_version,
        command_id=context.command_id,
        elapsed_ms=elapsed_ms,
        package_name=package_name,
        activity_name=activity_name,
        event_sequence=event_sequence,
        evidence_digest=digest,
        evidence=evidence,
        error_code=error_code,
    )


def _parse_evidence(
    command: str,
    values: Mapping[str, object],
    *,
    expected_username: str | None,
) -> HelperEvidence | Mapping[str, object]:
    if command == "health":
        return HelperHealth(
            service_enabled=_bool(values, "service_enabled"),
            tiktok_foreground=_bool(values, "tiktok_foreground"),
            surface=_string(values, "surface", allow_empty=True),
            busy=_bool(values, "busy"),
        )
    if command == "observe_profile":
        return _profile_evidence(values, expected_username=expected_username)
    if command == "open_video":
        return VideoEvidence(
            video_key=_string(values, "video_key"),
            post_resource_id=_string(values, "post_resource_id"),
            video_controls_visible=_bool(values, "video_controls_visible"),
        )
    if command in {"observe_action", "apply_action"}:
        action = _string(values, "action")
        control = _string(values, "control_resource_id")
        if command == "observe_action":
            state = _state(values, "state")
            return ActionEvidence(
                action=action, control_resource_id=control, state=state
            )
        return ActionEvidence(
            action=action,
            before=_state(values, "before"),
            after=_state(values, "after", allow_unknown=True),
            control_resource_id=control,
        )
    if command == "diagnostics":
        return HelperDiagnostics(
            node_count=_nonnegative_int(values, "node_count"),
            tree_age_ms=_nonnegative_int(values, "tree_age_ms"),
            visible_nodes=_nonnegative_int(values, "visible_nodes"),
        )
    return dict(values)


def _profile_evidence(
    values: Mapping[str, object], *, expected_username: str | None
) -> ProfileEvidence:
    access_state = _string(values, "access_state")
    if access_state not in {"available", "private", "unavailable"}:
        raise DeviceSideProtocolError("invalid_access_state")
    username = _string(values, "username", allow_empty=access_state != "available")
    if expected_username is not None and username != expected_username:
        raise DeviceSideProtocolError("target_identity_mismatch")
    following = _nonnegative_int(values, "following")
    followers = _nonnegative_int(values, "followers")
    video_count = _nonnegative_int(values, "video_count")
    raw_handles = values.get("post_handles")
    if not isinstance(raw_handles, list) or any(
        not isinstance(handle, str) or not handle for handle in raw_handles
    ):
        raise DeviceSideProtocolError("invalid_post_handles")
    handles = tuple(raw_handles)
    if len(set(handles)) != len(handles):
        raise DeviceSideProtocolError("duplicate_post_handle")
    if video_count != len(handles):
        raise DeviceSideProtocolError("video_count_mismatch")
    following_source = _string(
        values, "following_resource_id", allow_empty=access_state != "available"
    )
    followers_source = _string(
        values, "followers_resource_id", allow_empty=access_state != "available"
    )
    return ProfileEvidence(
        access_state=access_state,
        username=username,
        following=following,
        followers=followers,
        video_count=video_count,
        post_handles=handles,
        following_resource_id=following_source,
        followers_resource_id=followers_source,
    )


def _mapping(values: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = values.get(name)
    if not isinstance(value, Mapping):
        raise DeviceSideProtocolError(f"invalid_{name}")
    return value


def _string(
    values: Mapping[str, object], name: str, *, allow_empty: bool = False
) -> str:
    value = values.get(name)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise DeviceSideProtocolError(f"invalid_{name}")
    return value.strip()


def _nonnegative_int(values: Mapping[str, object], name: str) -> int:
    value = values.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeviceSideProtocolError(f"invalid_{name}")
    return value


def _bool(values: Mapping[str, object], name: str) -> bool:
    value = values.get(name)
    if not isinstance(value, bool):
        raise DeviceSideProtocolError(f"invalid_{name}")
    return value


def _equal(
    values: Mapping[str, object], name: str, expected: object, code: str
) -> None:
    if values.get(name) != expected or type(values.get(name)) is not type(expected):
        raise DeviceSideProtocolError(code)


def _state(
    values: Mapping[str, object], name: str, *, allow_unknown: bool = False
) -> str:
    state = _string(values, name)
    allowed = {"off", "on"}
    if allow_unknown:
        allowed.add("unknown")
    if state not in allowed:
        raise DeviceSideProtocolError(f"invalid_{name}")
    return state

import pytest

from tikpoc.acquisition_models import AssignmentPhase
from tikpoc.device_side_protocol import (
    ActionEvidence,
    CommandContext,
    DeviceSideProtocolError,
    HelperHealth,
    ProfileEvidence,
    build_request,
    parse_response,
)


def context() -> CommandContext:
    return CommandContext(
        command_id="cmd-1",
        device_id="device-1",
        account_id="account-1",
        fence_token=7,
        assignment_id=19,
        phase=AssignmentPhase.PROFILE_OPENING,
        deadline_monotonic_ms=9_000,
    )


def response(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "helper_version": "1.0.0",
        "command_id": "cmd-1",
        "device_id": "device-1",
        "account_id": "account-1",
        "fence_token": 7,
        "assignment_id": 19,
        "phase": "profile_opening",
        "status": "ok",
        "elapsed_ms": 31,
        "package_name": "com.zhiliaoapp.musically",
        "activity_name": "MainActivity",
        "event_sequence": 44,
        "evidence_digest": "sha256:abc",
        "evidence": {
            "service_enabled": True,
            "tiktok_foreground": True,
            "surface": "MainActivity",
            "busy": False,
        },
    }
    payload.update(overrides)
    return payload


def test_build_request_contains_complete_execution_context() -> None:
    payload = build_request(context(), command="health", arguments={})

    assert payload == {
        "version": 1,
        "command_id": "cmd-1",
        "command": "health",
        "device_id": "device-1",
        "account_id": "account-1",
        "fence_token": 7,
        "assignment_id": 19,
        "phase": "profile_opening",
        "deadline_elapsed_ms": 9_000,
        "arguments": {},
    }


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("command_id", "other", "command_id_mismatch"),
        ("device_id", "other", "device_id_mismatch"),
        ("account_id", "other", "account_id_mismatch"),
        ("fence_token", 8, "fence_token_mismatch"),
        ("assignment_id", 20, "assignment_id_mismatch"),
        ("phase", "identity_confirmed", "phase_mismatch"),
        ("helper_version", "2.0.0", "unsupported_helper_version"),
        ("package_name", "other.package", "unexpected_package"),
    ],
)
def test_response_rejects_mismatched_execution_context(
    field: str, value: object, code: str
) -> None:
    with pytest.raises(DeviceSideProtocolError, match=code):
        parse_response(
            response(**{field: value}),
            context=context(),
            command="health",
            now_monotonic_ms=2_000,
        )


def test_response_rejects_expired_deadline() -> None:
    with pytest.raises(DeviceSideProtocolError, match="deadline_expired"):
        parse_response(
            response(), context=context(), command="health", now_monotonic_ms=9_001
        )


def test_health_response_is_typed() -> None:
    parsed = parse_response(
        response(), context=context(), command="health", now_monotonic_ms=2_000
    )

    assert parsed.evidence == HelperHealth(
        service_enabled=True,
        tiktok_foreground=True,
        surface="MainActivity",
        busy=False,
    )


def test_profile_response_requires_complete_numeric_and_source_evidence() -> None:
    evidence = {
        "access_state": "available",
        "username": "target_user",
        "following": 120,
        "followers": 45,
        "video_count": 2,
        "post_handles": ["post:0", "post:1"],
        "following_resource_id": "following_count",
        "followers_resource_id": "followers_count",
    }
    parsed = parse_response(
        response(evidence=evidence),
        context=context(),
        command="observe_profile",
        now_monotonic_ms=2_000,
        expected_username="target_user",
    )
    assert parsed.evidence == ProfileEvidence(
        access_state="available",
        username="target_user",
        following=120,
        followers=45,
        video_count=2,
        post_handles=("post:0", "post:1"),
        following_resource_id="following_count",
        followers_resource_id="followers_count",
    )

    with pytest.raises(DeviceSideProtocolError, match="target_identity_mismatch"):
        parse_response(
            response(evidence={**evidence, "username": "other"}),
            context=context(),
            command="observe_profile",
            now_monotonic_ms=2_000,
            expected_username="target_user",
        )


def test_action_response_requires_before_after_and_unique_control() -> None:
    parsed = parse_response(
        response(
            evidence={
                "action": "like",
                "before": "off",
                "after": "on",
                "control_resource_id": "like_button",
            }
        ),
        context=context(),
        command="apply_action",
        now_monotonic_ms=2_000,
    )
    assert parsed.evidence == ActionEvidence(
        action="like", before="off", after="on", control_resource_id="like_button"
    )


def test_duplicate_post_handles_are_rejected() -> None:
    evidence = {
        "access_state": "available",
        "username": "target_user",
        "following": 1,
        "followers": 0,
        "video_count": 2,
        "post_handles": ["post:0", "post:0"],
        "following_resource_id": "following_count",
        "followers_resource_id": "followers_count",
    }
    with pytest.raises(DeviceSideProtocolError, match="duplicate_post_handle"):
        parse_response(
            response(evidence=evidence),
            context=context(),
            command="observe_profile",
            now_monotonic_ms=2_000,
            expected_username="target_user",
        )

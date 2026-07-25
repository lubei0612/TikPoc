from collections.abc import Mapping

import pytest

from tikpoc.acquisition_models import (
    ActionResult,
    AssignmentPhase,
    OutcomeKind,
    PoolTarget,
    ProfileAccessState,
)
from tikpoc.device_side_device import (
    DeviceSideEvidenceError,
    DeviceSideTikTokDevice,
)


class FakeTransport:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.responses: list[dict[str, object]] = []

    def request(self, payload: Mapping[str, object]) -> dict[str, object]:
        self.commands.append(dict(payload))
        return self.responses.pop(0)


def target() -> PoolTarget:
    return PoolTarget(
        pool_id="pool-1",
        identity_key="identity-1",
        target_id="target-id",
        sec_uid="sec-1",
        username="target_user",
        profile_url="https://www.tiktok.com/@target_user",
        source_video_id="",
        source_line_numbers=(),
        ordinal=0,
    )


def response(
    command_id: str, evidence: dict[str, object], **values: object
) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": 1,
        "helper_version": "1.0.0",
        "command_id": command_id,
        "device_id": "device-1",
        "account_id": "account-1",
        "fence_token": 7,
        "assignment_id": 19,
        "phase": "profile_opening",
        "status": "ok",
        "elapsed_ms": 10,
        "package_name": "com.zhiliaoapp.musically",
        "activity_name": "MainActivity",
        "event_sequence": 2,
        "evidence_digest": "sha256:test",
        "evidence": evidence,
    }
    payload.update(values)
    return payload


def device(transport: FakeTransport) -> DeviceSideTikTokDevice:
    instance = DeviceSideTikTokDevice(
        transport,
        device_id="device-1",
        monotonic_ms=lambda: 1_000,
        command_id_factory=lambda: f"cmd-{len(transport.commands) + 1}",
    )
    instance.bind_assignment(
        19, AssignmentPhase.PROFILE_OPENING, account_id="account-1", fence_token=7
    )
    return instance


def test_open_target_and_observe_profile_preserve_exact_business_evidence() -> None:
    transport = FakeTransport()
    transport.responses = [
        response("cmd-1", {"route_opened": True, "username": "target_user"}),
        response(
            "cmd-2",
            {
                "access_state": "available",
                "username": "target_user",
                "following": 120,
                "followers": 45,
                "video_count": 2,
                "post_handles": ["post:0", "post:1"],
                "following_resource_id": "following_count",
                "followers_resource_id": "followers_count",
            },
        ),
    ]
    adapter = device(transport)

    adapter.open_target(target())
    adapter.confirm_profile_identity(target())
    observation = adapter.read_profile_observation()

    assert observation.observed_username == "target_user"
    assert observation.metrics is not None
    assert observation.metrics.following == 120
    assert observation.metrics.followers == 45
    assert observation.metrics.posts == 2
    assert observation.access_state is ProfileAccessState.PUBLIC
    assert adapter.list_video_keys() == ("post:0", "post:1")
    assert transport.commands[0]["arguments"] == {
        "route": "snssdk1233://user/profile/target-id",
        "expected_username": "target_user",
    }


def test_profile_identity_mismatch_is_rejected() -> None:
    transport = FakeTransport()
    transport.responses = [
        response("cmd-1", {"route_opened": True, "username": "other"})
    ]
    with pytest.raises(DeviceSideEvidenceError, match="target_identity_mismatch"):
        device(transport).open_target(target())


def test_video_and_action_commands_keep_bound_phase_and_fence() -> None:
    transport = FakeTransport()
    transport.responses = [
        response(
            "cmd-1",
            {
                "video_key": "post:1",
                "post_resource_id": "video_item",
                "video_controls_visible": True,
            },
            phase="video_opening",
        ),
        response(
            "cmd-2",
            {
                "action": "like",
                "before": "off",
                "after": "on",
                "control_resource_id": "like_button",
            },
            phase="action_executing",
        ),
    ]
    adapter = device(transport)
    adapter.bind_assignment(
        19, AssignmentPhase.VIDEO_OPENING, account_id="account-1", fence_token=7
    )
    adapter.open_and_confirm_video("post:1")
    adapter.bind_assignment(
        19, AssignmentPhase.ACTION_EXECUTING, account_id="account-1", fence_token=7
    )
    assert adapter.execute_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
    assert transport.commands[1]["phase"] == "action_executing"
    assert transport.commands[1]["fence_token"] == 7


def test_trace_is_confirmed_without_helper_action() -> None:
    transport = FakeTransport()
    assert (
        device(transport).execute_outcome(OutcomeKind.TRACE) is ActionResult.CONFIRMED
    )
    assert transport.commands == []


def test_uncertain_action_reconciliation_is_read_only() -> None:
    transport = FakeTransport()
    transport.responses = [
        response(
            "cmd-1",
            {
                "action": "favorite",
                "before": "off",
                "after": "unknown",
                "control_resource_id": "favorite_button",
            },
            status="uncertain",
            phase="action_executing",
        ),
        response(
            "cmd-2",
            {
                "action": "favorite",
                "state": "on",
                "control_resource_id": "favorite_button",
            },
            phase="action_reconciling",
        ),
    ]
    adapter = device(transport)
    adapter.bind_assignment(
        19, AssignmentPhase.ACTION_EXECUTING, account_id="account-1", fence_token=7
    )
    assert adapter.execute_outcome(OutcomeKind.FAVORITE) is ActionResult.UNCERTAIN
    adapter.bind_assignment(
        19, AssignmentPhase.ACTION_RECONCILING, account_id="account-1", fence_token=7
    )
    assert adapter.reconcile_outcome(OutcomeKind.FAVORITE) is ActionResult.CONFIRMED
    assert transport.commands[1]["command"] == "observe_action"

import time
import uuid
from collections.abc import Callable, Mapping
from typing import Protocol

from .acquisition_models import (
    ActionResult,
    AssignmentPhase,
    DeviceDiagnostics,
    OutcomeKind,
    PoolTarget,
    ProfileAccessState,
    ProfileObservation,
)
from .device_performance import DevicePerformanceSnapshot
from .device_side_protocol import (
    ActionEvidence,
    CommandContext,
    DeviceSideProtocolError,
    HelperDiagnostics,
    HelperHealth,
    HelperResponse,
    ProfileEvidence,
    VideoEvidence,
    build_request,
    parse_response,
)
from .device_side_transport import DeviceSideTransportError
from .models import ProfileMetrics


class Transport(Protocol):
    def request(self, payload: Mapping[str, object]) -> dict[str, object]: ...


class DeviceSideUnavailable(RuntimeError):
    pass


class DeviceSideEvidenceError(ValueError):
    pass


def _monotonic_ms() -> int:
    return int(time.monotonic() * 1_000)


class DeviceSideTikTokDevice:
    def __init__(
        self,
        transport: Transport,
        *,
        device_id: str,
        monotonic_ms: Callable[[], int] = _monotonic_ms,
        command_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        command_timeout_ms: int = 10_000,
    ) -> None:
        if not device_id.strip() or command_timeout_ms <= 0:
            raise ValueError("invalid device-side adapter configuration")
        self.transport = transport
        self.device_id = device_id.strip()
        self.monotonic_ms = monotonic_ms
        self.command_id_factory = command_id_factory
        self.command_timeout_ms = command_timeout_ms
        self._assignment_id = 0
        self._phase = AssignmentPhase.PENDING
        self._account_id = ""
        self._fence_token = 0
        self._target: PoolTarget | None = None
        self._confirmed_username = ""
        self._profile: ProfileEvidence | None = None
        self._performance = DevicePerformanceSnapshot()

    def bind_assignment(
        self,
        assignment_id: int,
        phase: AssignmentPhase,
        *,
        account_id: str,
        fence_token: int,
    ) -> None:
        if assignment_id <= 0 or not account_id.strip() or fence_token <= 0:
            raise ValueError("device assignment context is incomplete")
        self._assignment_id = assignment_id
        self._phase = AssignmentPhase(phase)
        self._account_id = account_id.strip()
        self._fence_token = fence_token

    def ensure_ready(self) -> None:
        response = self._command("health", {})
        health = response.evidence
        if not isinstance(health, HelperHealth) or not (
            health.service_enabled and health.tiktok_foreground and not health.busy
        ):
            raise DeviceSideUnavailable("helper_not_ready")

    def open_target(self, target: PoolTarget) -> None:
        self._target = target
        self._profile = None
        self._confirmed_username = ""
        expected = _username(target.username)
        route = (
            f"snssdk1233://user/profile/{target.target_id}"
            if target.target_id
            else target.profile_url
        )
        response = self._command(
            "open_profile", {"route": route, "expected_username": expected}
        )
        evidence = response.evidence
        if (
            not isinstance(evidence, Mapping)
            or _username(str(evidence.get("username", ""))) != expected
        ):
            raise DeviceSideEvidenceError("target_identity_mismatch")
        self._confirmed_username = expected

    def confirm_profile_identity(self, target: PoolTarget) -> None:
        expected = _username(target.username)
        if self._target != target or self._confirmed_username != expected:
            raise DeviceSideEvidenceError("target_identity_mismatch")

    def read_profile_observation(self) -> ProfileObservation:
        expected = self._confirmed_username or (
            _username(self._target.username) if self._target is not None else None
        )
        response = self._command(
            "observe_profile",
            {"expected_username": expected},
            expected_username=expected,
        )
        evidence = response.evidence
        if not isinstance(evidence, ProfileEvidence):
            raise DeviceSideEvidenceError("incomplete_profile_evidence")
        self._profile = evidence
        access_state = {
            "available": ProfileAccessState.PUBLIC,
            "private": ProfileAccessState.PRIVATE,
            "unavailable": ProfileAccessState.MISSING,
        }[evidence.access_state]
        metrics = (
            ProfileMetrics(
                following=evidence.following,
                followers=evidence.followers,
                posts=evidence.video_count,
            )
            if access_state is ProfileAccessState.PUBLIC
            else None
        )
        return ProfileObservation(
            observed_username=evidence.username,
            metrics=metrics,
            private_account=access_state is ProfileAccessState.PRIVATE,
            access_state=access_state,
        )

    def list_video_keys(self) -> tuple[str, ...]:
        return self._profile.post_handles if self._profile is not None else ()

    def open_and_confirm_video(self, video_key: str) -> None:
        response = self._command("open_video", {"video_key": video_key})
        evidence = response.evidence
        if not isinstance(evidence, VideoEvidence) or (
            evidence.video_key != video_key or not evidence.video_controls_visible
        ):
            raise DeviceSideEvidenceError("video_not_verified")

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        normalized = OutcomeKind(outcome)
        if normalized is OutcomeKind.TRACE:
            return ActionResult.CONFIRMED
        response = self._command("apply_action", {"action": normalized.value})
        if response.status == "uncertain":
            return ActionResult.UNCERTAIN
        evidence = response.evidence
        if not isinstance(evidence, ActionEvidence):
            return ActionResult.UNCERTAIN
        return (
            ActionResult.CONFIRMED if evidence.after == "on" else ActionResult.UNCERTAIN
        )

    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult:
        normalized = OutcomeKind(outcome)
        if normalized is OutcomeKind.TRACE:
            return ActionResult.CONFIRMED
        response = self._command("observe_action", {"action": normalized.value})
        evidence = response.evidence
        if not isinstance(evidence, ActionEvidence):
            return ActionResult.UNCERTAIN
        if evidence.state == "on":
            return ActionResult.CONFIRMED
        if evidence.state == "off":
            return ActionResult.NOT_APPLIED
        return ActionResult.UNCERTAIN

    def capture_diagnostics(self) -> DeviceDiagnostics:
        try:
            response = self._command("diagnostics", {})
        except (DeviceSideUnavailable, DeviceSideEvidenceError):
            return DeviceDiagnostics(ui_summary="device-side diagnostics unavailable")
        evidence = response.evidence
        if not isinstance(evidence, HelperDiagnostics):
            return DeviceDiagnostics(ui_summary="device-side diagnostics incomplete")
        return DeviceDiagnostics(
            ui_summary=(
                f"nodes={evidence.node_count}; visible={evidence.visible_nodes}; "
                f"tree_age_ms={evidence.tree_age_ms}"
            )
        )

    def recover(self, phase: AssignmentPhase) -> None:
        self._phase = AssignmentPhase(phase)

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        return self._performance

    def _command(
        self,
        command: str,
        arguments: Mapping[str, object],
        *,
        expected_username: str | None = None,
    ) -> HelperResponse:
        if not self._assignment_id:
            raise DeviceSideUnavailable("assignment_context_missing")
        now_ms = self.monotonic_ms()
        context = CommandContext(
            command_id=self.command_id_factory(),
            device_id=self.device_id,
            account_id=self._account_id,
            fence_token=self._fence_token,
            assignment_id=self._assignment_id,
            phase=self._phase,
            deadline_monotonic_ms=now_ms + self.command_timeout_ms,
        )
        try:
            payload = self.transport.request(
                build_request(context, command=command, arguments=arguments)
            )
            completed_at_ms = self.monotonic_ms()
            response = parse_response(
                payload,
                context=context,
                command=command,
                now_monotonic_ms=completed_at_ms,
                expected_username=expected_username,
            )
        except DeviceSideTransportError as error:
            raise DeviceSideUnavailable(error.code) from None
        except DeviceSideProtocolError as error:
            if error.code in {
                "unsupported_helper_version",
                "unsupported_protocol_version",
                "unexpected_package",
            }:
                raise DeviceSideUnavailable(error.code) from None
            raise DeviceSideEvidenceError(error.code) from None
        current = self._performance
        self._performance = DevicePerformanceSnapshot(
            command_count=current.command_count,
            command_duration_ms=current.command_duration_ms,
            page_source_reads=current.page_source_reads,
            element_queries=current.element_queries,
            execute_script_calls=current.execute_script_calls,
            helper_command_count=current.helper_command_count + 1,
            helper_processing_ms=current.helper_processing_ms + response.elapsed_ms,
            host_round_trip_ms=current.host_round_trip_ms
            + max(0, completed_at_ms - now_ms),
            tree_age_ms=current.tree_age_ms + response.tree_age_ms,
            event_wait_ms=current.event_wait_ms + response.event_wait_ms,
            fallback_count=current.fallback_count,
            fallback_reason=current.fallback_reason,
        )
        if response.status == "error":
            code = response.error_code or "helper_error"
            if code in {"service_disabled", "helper_crashed", "version_mismatch"}:
                raise DeviceSideUnavailable(code)
            raise DeviceSideEvidenceError(code)
        return response


def _username(value: str) -> str:
    return value.strip().removeprefix("@").casefold()

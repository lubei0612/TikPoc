import hmac
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .acquisition_db import AcquisitionRepository
from .acquisition_service import (
    AcquisitionConflict,
    AcquisitionNotFound,
    AcquisitionService,
    merge_browser_health_rows,
)
from .api_models import (
    AccountAutomationSettingsCommand,
    AccountEnableCommand,
    BrowserActionClaimRequest,
    BrowserActionResultRequest,
    BrowserEventRequest,
    BrowserHealthRequest,
    BrowserIdentityRequest,
    BrowserReplyPlanRequest,
    BrowserReplyResultRequest,
    BrowserWelcomeResultRequest,
    CommentEvidenceRequest,
    CommentPlanApprovalRequest,
    CommentPlanRequest,
    CommentVideoRequest,
    DeviceEventRequest,
    FollowbackCooldownCommand,
    LeadCommand,
    LeadSaleCommand,
    LeadTakeoverCommand,
    LiveBatchRequest,
    ManualReplyPlanCommand,
    MobileHeartbeatRequest,
    MobilePullRequest,
    MobileRegisterRequest,
    MobileResultRequest,
    OperatorCommand,
    PoolImportRequest,
    ProviderSettingsCommand,
    RetryCommand,
    RoundCreateRequest,
)
from .browser_dm import BrowserConversationBusy, BrowserDmService, BrowserInbound
from .browser_welcome import BrowserWelcomeService
from .comment_sessions import CommentSessionService
from .db import Database, OperatorCommandConflict
from .device_api import MobileTaskResult
from .hot_comment_planner import CommentCandidate, CommentEvidence
from .live_batch_service import LiveBatchService, LiveTargetInput
from .messaging import RuntimeAiReplyClient, probe_openai_provider
from .runtime_metadata import runtime_metadata
from .runtime_settings import (
    AccountRuntimeSettings,
    ProviderCredentials,
    RuntimeSettingsStore,
)
from .web_accounts import WebAccount, WebAccountRegistry
from .webhooks import (
    WebhookPayloadError,
    WebhookSignatureError,
    parse_business_message_webhook,
    verify_tiktok_signature,
)

_BROWSER_PATHS = {
    "/api/browser-bindings",
    "/api/browser-events",
    "/api/browser-dm/reply-plan",
    "/api/browser-dm/reply-result",
    "/api/browser-dm/welcome-plan",
    "/api/browser-dm/welcome-result",
    "/api/browser-actions/claim",
    "/api/browser-actions/result",
    "/api/browser-health",
}
_SETTINGS_PREFIX = "/api/settings"
_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")
_CONSOLE_ASSET = re.compile(
    r"[A-Za-z0-9_.-]+-[A-Za-z0-9_-]+\.(?:css|gif|jpe?g|js|png|svg|webp|woff2?)"
)


class BrowserBindingConflict(ValueError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code

    @property
    def binding_state(self) -> str:
        return {
            "binding_mismatch": "mismatch",
            "binding_signed_out": "signed_out",
            "binding_verification_required": "verification_required",
        }.get(self.error_code, "unverified")


def _json(payload: object, status_code: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code)


async def _json_object(request: Request) -> dict[str, object]:
    try:
        payload = json.loads(await request.body())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid JSON") from error
    if not isinstance(payload, dict):
        raise TypeError("JSON body must be an object")
    return payload


def _browser_account(
    registry: WebAccountRegistry | None, identity: BrowserIdentityRequest
) -> WebAccount:
    if registry is None:
        raise ValueError("web account registry is not configured")
    account = registry.by_account_id(identity.account_id)
    if not account.enabled or account.device_id != identity.device_id:
        raise ValueError("browser account and device mapping do not match")
    if not account.expected_tiktok_username:
        raise BrowserBindingConflict("binding_unverified")
    if identity.binding_state != "ready":
        error_codes = {
            "mismatch": "binding_mismatch",
            "signed_out": "binding_signed_out",
            "verification_required": "binding_verification_required",
        }
        raise BrowserBindingConflict(
            error_codes.get(identity.binding_state, "binding_unverified")
        )
    expected = account.expected_tiktok_username.strip().removeprefix("@").casefold()
    observed = identity.observed_username.strip().removeprefix("@").casefold()
    if not observed:
        raise BrowserBindingConflict("binding_unverified")
    if observed != expected:
        raise BrowserBindingConflict("binding_mismatch")
    return account


def _record_browser_health(
    database: Database,
    body: BrowserHealthRequest,
    *,
    received_at_ms: int,
    binding_state: str | None = None,
) -> None:
    clock_skew_ms = max(0, body.timestamp_ms - int(received_at_ms))

    def server_time(client_time_ms: int) -> int:
        return max(0, int(client_time_ms) - clock_skew_ms) if client_time_ms else 0

    database.upsert_browser_health(
        body.account_id,
        body.page_role,
        device_id=body.device_id,
        status=binding_state or body.binding_state,
        observed_at_ms=server_time(body.timestamp_ms),
        detail=body.path,
        observed_username=body.observed_username,
        last_scan_at_ms=server_time(body.last_scan_at_ms),
        last_success_at_ms=server_time(body.last_success_at_ms),
        scan_state=body.scan_state,
    )
    database.record_runtime_event(f"browser_health_{body.page_role}", body.account_id)


def create_app(
    database_path: Path,
    *,
    registry: WebAccountRegistry | None = None,
    browser_dm_service: BrowserDmService | None = None,
    browser_welcome_service: BrowserWelcomeService | None = None,
    browser_extension_origins: Iterable[str] | None = None,
    tiktok_app_secret: str = "",
    webhook_max_age_seconds: int = 300,
    clock: Callable[[], float] = time.time,
    import_roots: Iterable[Path] | None = None,
    runtime_settings: RuntimeSettingsStore | None = None,
    provider_tester: Callable[[ProviderCredentials], tuple[bool, int]] | None = None,
    mobile_bootstrap_token: str = "",
    live_batch_token: str = "",
    comment_submission_interval_ms: int = 40 * 60 * 1_000,
    comment_submission_jitter_ms: int = 25 * 60 * 1_000,
) -> FastAPI:
    database = Database(database_path)
    database.migrate()
    acquisition = AcquisitionRepository(
        database_path, clock_ms=lambda: int(clock() * 1_000)
    )
    acquisition.migrate()
    acquisition_service = AcquisitionService(
        acquisition,
        clock_ms=lambda: int(clock() * 1000),
        import_roots=tuple(import_roots or (database_path.parent,)),
        browser_accounts=() if registry is None else registry.accounts,
    )
    acquisition_service.migrate()
    comment_sessions = CommentSessionService(
        acquisition,
        clock_ms=lambda: int(clock() * 1000),
        submission_interval_ms=comment_submission_interval_ms,
        submission_jitter_ms=comment_submission_jitter_ms,
    )
    live_batches = LiveBatchService(acquisition)
    runtime_settings = runtime_settings or RuntimeSettingsStore(
        database_path.parent / "config" / "secrets" / "operator-settings.json"
    )
    provider_tester = provider_tester or probe_openai_provider

    def runtime_account(account: WebAccount) -> WebAccount:
        settings = runtime_settings.account_settings(account.account_id)
        return replace(
            account,
            whatsapp=settings.whatsapp or account.whatsapp,
            telegram=settings.telegram or account.telegram,
            offer_context=settings.offer_context or account.offer_context,
            faq_text=settings.faq_context or account.faq_text,
            reply_tone=settings.reply_tone or account.reply_tone,
            brand_name=settings.brand_name or account.brand_name,
            welcome_after_followback=settings.welcome_after_followback,
            welcome_language=settings.welcome_language or account.welcome_language,
        )

    if browser_dm_service is None and registry is not None:
        browser_dm_service = BrowserDmService(
            database,
            registry,
            RuntimeAiReplyClient(runtime_settings.provider_credentials),
            account_overlay=runtime_account,
        )
    if browser_welcome_service is None and registry is not None:
        browser_welcome_service = BrowserWelcomeService(
            database,
            registry,
            RuntimeAiReplyClient(runtime_settings.provider_credentials),
            clock=clock,
            account_overlay=runtime_account,
        )
    if browser_extension_origins is None:
        browser_extension_origins = os.getenv(
            "TIKPOC_BROWSER_EXTENSION_ORIGINS", ""
        ).split(",")
    browser_origins = {
        origin.strip()
        for origin in browser_extension_origins
        if _EXTENSION_ORIGIN.fullmatch(origin.strip())
    }

    app = FastAPI(title="TikPoc Operator API", docs_url=None, redoc_url=None)
    app.state.database = database
    app.state.acquisition = acquisition
    app.state.acquisition_service = acquisition_service
    app.state.comment_sessions = comment_sessions
    app.state.live_batches = live_batches
    app.state.registry = registry
    app.state.browser_dm_service = browser_dm_service
    app.state.browser_welcome_service = browser_welcome_service
    app.state.runtime_settings = runtime_settings
    app.state.browser_origins = browser_origins
    app.state.tiktok_app_secret = tiktok_app_secret
    app.state.webhook_max_age_seconds = webhook_max_age_seconds
    app.state.clock = clock
    app.state.mobile_bootstrap_token = mobile_bootstrap_token
    app.state.live_batch_token = live_batch_token

    def bearer_token(request: Request) -> str:
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if separator != " " or scheme.casefold() != "bearer" or not token.strip():
            return ""
        return token.strip()

    def claim_comment_task(
        body: MobilePullRequest, session
    ) -> dict[str, object] | None:
        if database.worker_control() != "running":
            return None
        if comment_sessions.device_block(body.device_id) is not None:
            return None
        plan = comment_sessions.claim_for_account(
            session.account_id,
            f"mobile:{body.device_id}:{body.session_epoch}",
            include_reconciliation=True,
        )
        if plan is None:
            return None
        video = comment_sessions.video(plan.video_id)
        return {
            "task_kind": "brand_comment",
            "task_id": f"comment:{plan.plan_id}",
            "plan_id": plan.plan_id,
            "attempt_id": f"comment:{plan.plan_id}",
            "device_id": body.device_id,
            "account_id": session.account_id,
            "session_epoch": body.session_epoch,
            "lease_id": f"comment:{plan.plan_id}:{body.session_epoch}",
            "lease_expires_at_ms": int(clock() * 1_000) + 120_000,
            "phase": (
                "comment_reconciling"
                if plan.state in {"submitted", "uncertain"}
                else "video_opening"
            ),
            "video_id": plan.video_id,
            "video_url": video.source_url,
            "creator_username": video.creator_username,
            "caption_anchor": video.caption_anchor,
            "publish_text": plan.english,
        }

    @app.middleware("http")
    async def browser_request_gate(request: Request, call_next: Callable):
        path = request.url.path
        if path.startswith(_SETTINGS_PREFIX):
            host = (request.url.hostname or "").casefold()
            if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
                return _json({"error": "settings are available on loopback only"}, 403)
            if request.method == "POST":
                origin = request.headers.get("origin", "").rstrip("/")
                expected_origin = (
                    f"{request.url.scheme}://{request.headers.get('host', '')}"
                )
                if origin != expected_origin:
                    return _json({"error": "settings origin is not allowed"}, 403)
                media_type = request.headers.get("content-type", "").split(";", 1)[0]
                if media_type.strip().casefold() != "application/json":
                    return _json(
                        {"error": "settings request must use application/json"}, 415
                    )
            return await call_next(request)
        if path not in _BROWSER_PATHS:
            return await call_next(request)
        origin = request.headers.get("origin")
        extension_identity = request.query_params.get("extension_origin")
        allowed_origin = origin if origin in browser_origins else None
        allowed_extension_identity = (
            extension_identity
            if origin is None and extension_identity in browser_origins
            else None
        )
        browser_identity_verified = bool(allowed_origin or allowed_extension_identity)
        if request.method == "OPTIONS":
            if allowed_origin is None:
                return Response(status_code=404)
            response: Response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, OPTIONS" if path == "/api/browser-bindings" else "POST, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        elif request.method == "POST":
            if not browser_identity_verified:
                return _json({"error": "browser origin is not allowed"}, 403)
            media_type = request.headers.get("content-type", "").split(";", 1)[0]
            if media_type.strip().lower() != "application/json":
                response = _json(
                    {"error": "browser request must use application/json"}, 415
                )
            else:
                response = await call_next(request)
        elif request.method == "GET" and not browser_identity_verified:
            return _json({"error": "browser origin is not allowed"}, 403)
        else:
            response = await call_next(request)
        if allowed_origin is not None:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/api/status")
    def status() -> JSONResponse:
        payload = database.dashboard_snapshot()
        payload["latest_event"] = database.latest_runtime_event()
        return _json(payload)

    @app.get("/api/runtime")
    def runtime() -> JSONResponse:
        return _json(runtime_metadata())

    @app.get("/api/recent")
    def recent(limit: str = "10") -> JSONResponse:
        try:
            parsed_limit = int(limit)
        except ValueError:
            parsed_limit = 10
        return _json(database.recent_tasks(parsed_limit))

    @app.post("/api/control/{action}")
    def control(action: str) -> JSONResponse:
        states = {"pause": "paused", "resume": "running", "stop": "stopped"}
        if action not in states:
            return _json({"detail": "Not Found"}, 404)
        state = states[action]
        database.set_worker_control(state)
        return _json({"control": state})

    @app.post("/api/device-events")
    async def device_event(request: Request) -> JSONResponse:
        try:
            body = DeviceEventRequest.model_validate(await _json_object(request))
            accepted = database.enqueue_device_event(
                body.device_id,
                body.event_type,
                body.dedup_key,
                body.payload,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid device event"}, 400)
        return _json({"accepted": accepted})

    @app.post("/api/mobile/register")
    async def mobile_register(request: Request) -> JSONResponse:
        supplied = bearer_token(request)
        if (
            not supplied
            or not mobile_bootstrap_token
            or not hmac.compare_digest(supplied, mobile_bootstrap_token)
        ):
            return _json({"error": "invalid_bootstrap_token"}, 401)
        try:
            body = MobileRegisterRequest.model_validate(await _json_object(request))
            session = acquisition.register_mobile_device(
                body.device_id, body.account_id, now_ms=int(clock() * 1_000)
            )
        except ValidationError:
            return _json({"error": "invalid_mobile_registration"}, 400)
        except ValueError as error:
            return _json({"error": str(error)}, 409)
        return _json(
            {
                "device_id": session.device_id,
                "account_id": session.account_id,
                "session_epoch": session.session_epoch,
                "access_token": session.access_token,
            }
        )

    @app.post("/api/comment-videos")
    async def comment_video_add(request: Request) -> JSONResponse:
        try:
            body = CommentVideoRequest.model_validate(await _json_object(request))
            video = comment_sessions.add_video(
                body.source_url,
                creator_username=body.creator_username,
                caption_anchor=body.caption_anchor,
            )
        except (TypeError, ValueError, ValidationError) as error:
            return _json({"error": str(error)}, 400)
        return _json(
            {
                "video_id": video.video_id,
                "source_url": video.source_url,
                "creator_username": video.creator_username,
                "caption_anchor": video.caption_anchor,
            }
        )

    @app.post("/api/comment-videos/{video_id}/evidence")
    async def comment_evidence_import(video_id: str, request: Request) -> JSONResponse:
        try:
            body = CommentEvidenceRequest.model_validate(await _json_object(request))
            imported = comment_sessions.import_evidence(
                video_id,
                [
                    CommentEvidence(
                        item.cid,
                        item.text,
                        item.digg_count,
                        item.reply_comment_total,
                        item.create_time,
                        item.language,
                    )
                    for item in body.comments
                ],
            )
        except (TypeError, ValueError, ValidationError) as error:
            return _json({"error": str(error)}, 400)
        return _json({"imported": imported, "video_id": video_id})

    @app.post("/api/comment-plans")
    async def comment_plan_create(request: Request) -> JSONResponse:
        try:
            body = CommentPlanRequest.model_validate(await _json_object(request))
            comment_sessions.save_persona(
                body.persona_id, body.account_id, body.display_name
            )
            candidate = comment_sessions.save_candidate(
                body.video_id,
                CommentCandidate(
                    body.english,
                    body.chinese,
                    body.emoji_count,
                    body.persona_id,
                ),
                command_id=body.command_id,
            )
        except (TypeError, ValueError, ValidationError) as error:
            return _json({"error": str(error)}, 400)
        return _json({"plan_id": candidate.candidate_id, "state": "draft"})

    @app.post("/api/comment-plans/{plan_id}/approve")
    async def comment_plan_approve(plan_id: int, request: Request) -> JSONResponse:
        try:
            body = CommentPlanApprovalRequest.model_validate(
                await _json_object(request)
            )
        except (TypeError, ValueError, ValidationError) as error:
            return _json({"error": str(error)}, 400)
        try:
            plan = comment_sessions.plan(plan_id)
        except ValueError:
            plan = None
        try:
            if plan is None:
                with acquisition._connect_read_only() as connection:
                    row = connection.execute(
                        "SELECT video_id FROM comment_plans WHERE plan_id = ?",
                        (plan_id,),
                    ).fetchone()
                if row is None:
                    return _json({"error": "plan_not_found"}, 404)
                plan = comment_sessions.approve_plan(
                    body.account_id, str(row["video_id"]), plan_id
                )
            elif plan.account_id != body.account_id:
                return _json({"error": "persona_account_mismatch"}, 409)
        except ValueError as error:
            return _json({"error": str(error)}, 409)
        return _json(
            {
                "plan_id": plan.plan_id,
                "video_id": plan.video_id,
                "account_id": plan.account_id,
                "state": plan.state,
            }
        )

    @app.get("/api/comment-plans")
    def comment_plan_status() -> JSONResponse:
        return _json(comment_sessions.list_plans())

    @app.post("/api/live-batches")
    async def live_batch_submit(request: Request) -> JSONResponse:
        supplied = bearer_token(request)
        if (
            not supplied
            or not live_batch_token
            or not hmac.compare_digest(supplied, live_batch_token)
        ):
            return _json({"error": "invalid_live_batch_token"}, 401)
        try:
            body = LiveBatchRequest.model_validate(await _json_object(request))
            summary = live_batches.submit(
                host_round_id=body.host_round_id,
                source_live_id=body.source_live_id,
                navigation_mode=body.navigation_mode,
                targets=tuple(
                    LiveTargetInput(
                        username=item.username,
                        sec_uid=item.sec_uid,
                        uid=item.uid,
                        source_video_id=item.source_video_id,
                        collected_at_ms=item.collected_at_ms,
                    )
                    for item in body.targets
                ),
            )
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            return _json({"error": str(error)}, 400)
        return _json(
            {
                "batch_id": summary.batch_id,
                "source_live_id": summary.source_live_id,
                "unique_targets": summary.unique_targets,
                "skipped_duplicates": summary.skipped_duplicates,
                "skipped_invalid": summary.skipped_invalid,
                "device_count": summary.device_count,
                "navigation_mode": summary.navigation_mode,
            }
        )

    @app.post("/api/mobile/heartbeat")
    async def mobile_heartbeat(request: Request) -> JSONResponse:
        try:
            body = MobileHeartbeatRequest.model_validate(await _json_object(request))
        except (TypeError, ValueError, ValidationError):
            return _json({"error": "invalid_mobile_heartbeat"}, 400)
        session = acquisition.authenticate_mobile_device(
            body.device_id,
            bearer_token(request),
            now_ms=int(clock() * 1_000),
        )
        if session is None:
            return _json({"error": "invalid_mobile_token"}, 401)
        if session.session_epoch != body.session_epoch:
            return _json({"error": "stale_session"}, 409)
        if body.phase == "stable_home" and not comment_sessions.complete_stable_home(
            body.device_id, session.account_id
        ):
            return _json({"error": "recovery_not_acknowledged"}, 409)
        accepted = acquisition.record_mobile_heartbeat(
            body.device_id,
            body.session_epoch,
            app_version=body.app_version,
            phase=body.phase,
            queue_depth=body.queue_depth,
            client_timestamp_ms=body.client_timestamp_ms,
            now_ms=int(clock() * 1_000),
        )
        if not accepted:
            return _json({"error": "stale_session"}, 409)
        return _json({"accepted": True, "server_time_ms": int(clock() * 1_000)})

    @app.post("/api/mobile/pull")
    async def mobile_pull(request: Request) -> JSONResponse:
        try:
            body = MobilePullRequest.model_validate(await _json_object(request))
        except (TypeError, ValueError, ValidationError):
            return _json({"error": "invalid_mobile_pull"}, 400)
        session = acquisition.authenticate_mobile_device(
            body.device_id, bearer_token(request), now_ms=int(clock() * 1_000)
        )
        if session is None:
            return _json({"error": "invalid_mobile_token"}, 401)
        if session.session_epoch != body.session_epoch:
            return _json({"error": "stale_session"}, 409)
        if body.task_kind == "brand_comment":
            task = claim_comment_task(body, session)
            return _json({"tasks": [] if task is None else [task]})
        if not body.round_id:
            return _json({"error": "round_id is required"}, 400)
        try:
            tasks = (
                acquisition.claim_mobile_priority_tasks(
                    body.round_id,
                    body.device_id,
                    session_epoch=body.session_epoch,
                    limit=body.limit,
                    now_ms=int(clock() * 1_000),
                )
                if body.task_kind == "hybrid"
                else acquisition.claim_mobile_tasks(
                    body.round_id,
                    body.device_id,
                    session_epoch=body.session_epoch,
                    limit=body.limit,
                    now_ms=int(clock() * 1_000),
                )
            )
        except ValueError as error:
            return _json({"error": str(error)}, 409)
        if body.task_kind == "hybrid" and not tasks:
            if acquisition.live_interrupt_pending(body.round_id):
                return _json({"tasks": []})
            task = claim_comment_task(body, session)
            return _json({"tasks": [] if task is None else [task]})
        return _json(
            {
                "tasks": [
                    {
                        "task_kind": "profile_touch",
                        "task_id": task.task_id,
                        "assignment_id": task.assignment_id,
                        "round_id": task.round_id,
                        "device_id": task.device_id,
                        "account_id": task.account_id,
                        "session_epoch": task.session_epoch,
                        "lease_id": task.lease_id,
                        "lease_expires_at_ms": task.lease_expires_at_ms,
                        "phase": task.phase,
                        "target_id": task.target_id,
                        "username": task.username,
                        "profile_url": task.profile_url,
                        "navigation_mode": task.navigation_mode,
                        "plan_id": task.plan_id,
                        "action": task.action,
                        "video_key": task.video_key,
                    }
                    for task in tasks
                ]
            }
        )

    @app.post("/api/mobile/results")
    async def mobile_result(request: Request) -> JSONResponse:
        try:
            body = MobileResultRequest.model_validate(await _json_object(request))
        except (TypeError, ValueError, ValidationError):
            return _json({"error": "invalid_mobile_result"}, 400)
        session = acquisition.authenticate_mobile_device(
            body.device_id, bearer_token(request), now_ms=int(clock() * 1_000)
        )
        if session is None:
            return _json({"error": "invalid_mobile_token"}, 401)
        if session.session_epoch != body.session_epoch:
            return _json({"error": "stale_session"}, 409)
        if body.task_id.startswith("comment:"):
            try:
                plan_id = int(body.task_id.split(":", 1)[1])
                plan = comment_sessions.plan(plan_id)
                expected_lease = f"comment:{plan_id}:{body.session_epoch}"
                if (
                    plan.account_id != session.account_id
                    or body.lease_id != expected_lease
                ):
                    return _json({"error": "comment_lease_mismatch"}, 409)
                if body.evidence.get("error_code") == "verification_required":
                    comment_sessions.record_verification_required(
                        body.device_id,
                        session.account_id,
                        plan_id,
                        phase=body.phase,
                        event_key=body.idempotency_key,
                    )
                    return _json(
                        {
                            "accepted": True,
                            "state": "accepted",
                            "comment_state": "verification_required",
                        }
                    )
                visible = bool(body.evidence.get("visible_confirmed"))
                error_code = str(
                    body.evidence.get("error_code") or body.evidence.get("code") or ""
                ).strip()[:100]
                pre_submit_route_failure = error_code in {
                    "comment_video_not_verified",
                    "video_open_rejected",
                    "comment_post_control_missing",
                    "comment_text_input_failed",
                }
                if not visible and pre_submit_route_failure and plan.state == "leased":
                    comment_sessions.record_pre_submit_skip(
                        plan_id,
                        body.idempotency_key,
                        error_code=error_code,
                    )
                    state = "skipped"
                elif plan.state in {"submitted", "uncertain"}:
                    comment_sessions.record_reconciliation(
                        plan_id, body.idempotency_key, visible=visible
                    )
                    state = "visible_confirmed" if visible else "uncertain"
                else:
                    state = "visible_confirmed" if visible else "uncertain"
                    comment_sessions.record_submission(
                        plan_id,
                        body.idempotency_key,
                        state=state,
                        error_code=error_code,
                    )
            except (TypeError, ValueError) as error:
                return _json({"error": str(error)}, 409)
            return _json(
                {"accepted": True, "state": "accepted", "comment_state": state}
            )
        state = acquisition.record_mobile_result(
            MobileTaskResult(
                device_id=body.device_id,
                session_epoch=body.session_epoch,
                task_id=body.task_id,
                lease_id=body.lease_id,
                idempotency_key=body.idempotency_key,
                state=body.state,
                phase=body.phase,
                evidence=body.evidence,
            ),
            now_ms=int(clock() * 1_000),
        )
        if state == "stale_session":
            return _json({"error": state}, 409)
        return _json({"accepted": state == "accepted", "state": state})

    @app.post("/api/comment-recovery/{device_id}/acknowledge")
    async def comment_recovery_acknowledge(
        device_id: str, request: Request
    ) -> JSONResponse:
        try:
            payload = await _json_object(request)
            command_id = str(payload.get("command_id", "")).strip()
            if not command_id:
                raise ValueError("command_id is required")
            return _json(
                comment_sessions.acknowledge_recovery(device_id, command_id=command_id)
            )
        except (TypeError, ValueError) as error:
            return _json({"error": str(error)}, 409)

    @app.get("/api/comment-metrics")
    def comment_metrics(
        account_id: str = Query(min_length=1, max_length=200),
    ) -> JSONResponse:
        metrics = comment_sessions.metrics(account_id)
        funnel = database.lead_funnel_snapshot(account_ids=(account_id,))
        metrics.update(
            {
                "profile_visits": 0,
                "follows": int(funnel.get("followers", 0)),
                "inbound_messages": int(funnel.get("engaged", 0)),
                "qualified_leads": int(funnel.get("qualified", 0)),
            }
        )
        return _json(metrics)

    @app.post("/api/browser-events")
    async def browser_event(request: Request) -> JSONResponse:
        try:
            body = BrowserEventRequest.model_validate(await _json_object(request))
            _browser_account(registry, body)
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser event"}, 400)
        payload = dict(body.payload)
        payload["device_id"] = body.device_id
        accepted = database.enqueue_web_event(
            body.account_id,
            body.event_type,
            body.dedup_key,
            payload,
        )
        return _json({"accepted": accepted})

    @app.get("/api/browser-bindings")
    def browser_bindings() -> JSONResponse:
        accounts = () if registry is None else registry.accounts

        def binding_payload(account: WebAccount) -> dict[str, object]:
            settings = database.account_operator_settings(
                account.account_id,
                default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                default_followback_enabled=(
                    account.enabled and account.browser_followback_enabled
                ),
            )
            circuit = database.browser_action_circuit(
                account.account_id, "followback", now_ms=int(clock() * 1_000)
            )
            return {
                "account_id": account.account_id,
                "device_id": account.device_id,
                "expected_tiktok_username": account.expected_tiktok_username,
                "browser_profile_label": account.browser_profile_label,
                "enabled": account.enabled,
                "browser_followback_enabled": bool(
                    account.enabled and settings["followback_enabled"]
                ),
                "browser_dm_enabled": bool(account.enabled and settings["ai_enabled"]),
                "binding_ready": bool(account.expected_tiktok_username),
                "followback_circuit_state": circuit["state"],
                "followback_circuit_reason": circuit["reason"],
                "followback_cooldown_until_ms": circuit["cooldown_until_ms"],
            }

        return _json({"accounts": [binding_payload(account) for account in accounts]})

    @app.post("/api/browser-dm/reply-plan")
    async def browser_reply_plan(request: Request) -> JSONResponse:
        if browser_dm_service is None:
            return _json({"error": "browser DM service is not configured"}, 503)
        try:
            body = BrowserReplyPlanRequest.model_validate(await _json_object(request))
            account = _browser_account(registry, body)
            settings = database.account_operator_settings(
                account.account_id,
                default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                default_followback_enabled=(
                    account.enabled and account.browser_followback_enabled
                ),
            )
            if not settings["ai_enabled"] or not database.conversation_ai_available(
                body.account_id, body.conversation_id
            ):
                return _json({"error": "AI replies are disabled"}, 409)
            reply = await run_in_threadpool(
                browser_dm_service.plan,
                BrowserInbound(
                    account_id=body.account_id,
                    device_id=body.device_id,
                    conversation_id=body.conversation_id,
                    fingerprint=body.fingerprint,
                    participant_username=body.participant_username,
                    text=body.text,
                    timestamp_ms=body.timestamp_ms,
                ),
            )
        except BrowserConversationBusy:
            return _json({"error": "browser conversation busy"}, 409)
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json(
            {
                "plan_id": reply.plan_id,
                "conversation_id": reply.conversation_id,
                "inbound_fingerprint": reply.inbound_fingerprint,
                "reply_text": reply.reply_text,
                "stage": reply.stage,
            }
        )

    @app.post("/api/browser-dm/reply-result")
    async def browser_reply_result(request: Request) -> JSONResponse:
        if browser_dm_service is None:
            return _json({"error": "browser DM service is not configured"}, 503)
        try:
            body = BrowserReplyResultRequest.model_validate(await _json_object(request))
            _browser_account(registry, body)
            recorded = browser_dm_service.record_result(
                body.account_id,
                body.device_id,
                body.plan_id,
                body.state,
            )
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except KeyError:
            return _json({"error": "browser reply plan not found"}, 404)
        except (TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json({"recorded": recorded})

    @app.post("/api/browser-dm/welcome-plan")
    async def browser_welcome_plan(request: Request) -> Response:
        if browser_welcome_service is None:
            return _json({"error": "browser welcome service is not configured"}, 503)
        try:
            body = BrowserIdentityRequest.model_validate(await _json_object(request))
            _browser_account(registry, body)
            plan = await run_in_threadpool(
                browser_welcome_service.next_plan,
                body.account_id,
                body.device_id,
            )
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        if plan is None:
            return Response(status_code=204)
        return _json(
            {
                "plan_id": plan.id,
                "follower_username": plan.follower_username,
                "reply_text": plan.reply_text,
            }
        )

    @app.post("/api/browser-dm/welcome-result")
    async def browser_welcome_result(request: Request) -> JSONResponse:
        if browser_welcome_service is None:
            return _json({"error": "browser welcome service is not configured"}, 503)
        try:
            body = BrowserWelcomeResultRequest.model_validate(
                await _json_object(request)
            )
            _browser_account(registry, body)
            recorded = browser_welcome_service.record_result(
                body.account_id,
                body.device_id,
                body.plan_id,
                body.state,
            )
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json({"recorded": recorded})

    @app.post("/api/browser-actions/claim")
    async def browser_action_claim(request: Request) -> JSONResponse:
        try:
            body = BrowserActionClaimRequest.model_validate(await _json_object(request))
            account = _browser_account(registry, body)
            if body.action_type == "dm_send":
                claimed = database.claim_browser_dm_action(
                    body.account_id,
                    body.action_key,
                    body.owner_id,
                    body.timestamp_ms,
                    body.lease_seconds,
                    default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                    account_ai_allowed=(account.enabled and account.browser_dm_enabled),
                )
            elif body.action_type == "welcome_send":
                claimed = database.claim_browser_welcome_action(
                    body.account_id,
                    body.action_key,
                    body.owner_id,
                    body.timestamp_ms,
                    body.lease_seconds,
                )
            else:
                settings = database.account_operator_settings(
                    account.account_id,
                    default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                    default_followback_enabled=(
                        account.enabled and account.browser_followback_enabled
                    ),
                )
                if (
                    body.action_type == "followback"
                    and not settings["followback_enabled"]
                ):
                    return _json({"claimed": False})
                if body.action_type == "followback":
                    follower_username = database.browser_follower_username(
                        body.account_id, body.action_key
                    )
                    if follower_username and not database.browser_contact_allowed(
                        body.account_id, follower_username
                    ):
                        return _json({"claimed": False})
                    claimed = database.claim_browser_followback_action(
                        body.account_id,
                        body.action_key,
                        body.owner_id,
                        body.timestamp_ms,
                        body.lease_seconds,
                    )
                    if not claimed:
                        circuit = database.browser_action_circuit(
                            body.account_id,
                            "followback",
                            now_ms=body.timestamp_ms,
                        )
                        if circuit["state"] != "closed":
                            return _json(
                                {
                                    "claimed": False,
                                    "circuit_state": circuit["state"],
                                }
                            )
                else:
                    claimed = database.claim_browser_action(
                        body.account_id,
                        body.action_type,
                        body.action_key,
                        body.owner_id,
                        body.timestamp_ms,
                        body.lease_seconds,
                    )
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json({"claimed": claimed})

    @app.post("/api/browser-actions/result")
    async def browser_action_result(request: Request) -> JSONResponse:
        try:
            body = BrowserActionResultRequest.model_validate(
                await _json_object(request)
            )
            _browser_account(registry, body)
            if body.action_type == "followback":
                recorded = database.finish_browser_followback_action(
                    body.account_id,
                    body.action_key,
                    body.owner_id,
                    body.state,
                    reason=body.reason,
                    now_ms=int(clock() * 1_000),
                )
            else:
                recorded = database.finish_browser_action(
                    body.account_id,
                    body.action_type,
                    body.action_key,
                    body.owner_id,
                    body.state,
                )
            if (
                recorded
                and body.action_type == "followback"
                and body.state == "completed"
                and browser_welcome_service is not None
            ):
                await run_in_threadpool(
                    browser_welcome_service.plan_after_followback,
                    body.account_id,
                    body.device_id,
                    body.action_key,
                )
        except BrowserBindingConflict as error:
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json({"recorded": recorded})

    @app.post("/api/browser-health")
    async def browser_health(request: Request) -> JSONResponse:
        try:
            body = BrowserHealthRequest.model_validate(await _json_object(request))
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        try:
            _browser_account(registry, body)
        except BrowserBindingConflict as error:
            _record_browser_health(
                database,
                body,
                received_at_ms=int(clock() * 1_000),
                binding_state=error.binding_state,
            )
            return _json({"error": error.error_code}, 409)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        _record_browser_health(
            database,
            body,
            received_at_ms=int(clock() * 1_000),
        )
        return _json({"recorded": True})

    @app.post("/api/tiktok-business/webhook")
    async def tiktok_business_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        if not tiktok_app_secret:
            return _json({"error": "webhook is not configured"}, 503)
        try:
            verify_tiktok_signature(
                body,
                request.headers.get("TikTok-Signature", ""),
                tiktok_app_secret,
                now=int(clock()),
                max_age_seconds=webhook_max_age_seconds,
            )
        except WebhookSignatureError:
            return _json({"error": "invalid signature"}, 401)
        try:
            event = parse_business_message_webhook(body)
        except WebhookPayloadError:
            return _json({"accepted": False, "ignored": "invalid payload"})
        if event is None:
            return _json({"accepted": False, "ignored": "unsupported event"})
        if registry is None:
            return _json({"error": "web account registry is not configured"}, 503)
        try:
            account = registry.by_business_id(event.business_id)
        except KeyError:
            database.record_runtime_event("unknown_business_account")
            return _json(
                {"accepted": False, "ignored": "unknown business account"}, 202
            )
        if not account.enabled:
            return _json({"accepted": False, "ignored": "account disabled"})
        payload: dict[str, object] = {
            "business_id": event.business_id,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "sender_id": event.sender_id,
            "sender_username": event.sender_username,
            "text": event.text,
            "message_type": event.message_type,
            "timestamp_ms": event.timestamp_ms,
            "source": event.source,
        }
        if event.is_follower is not None:
            payload["is_follower"] = event.is_follower
        accepted = database.enqueue_web_event(
            account.account_id,
            "dm_received",
            event.message_id,
            payload,
        )
        return _json({"accepted": accepted})

    @app.get("/api/pools")
    def acquisition_pools(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        return _json(acquisition_service.pools(offset=offset, limit=limit))

    @app.post("/api/pools/import")
    def acquisition_pool_import(body: PoolImportRequest) -> JSONResponse:
        try:
            return _json(acquisition_service.import_pool(body.local_path))
        except AcquisitionConflict as error:
            return _json({"error": str(error)}, 409)
        except (OSError, UnicodeError, ValueError) as error:
            return _json({"error": str(error)}, 400)

    @app.get("/api/rounds")
    def acquisition_rounds(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        return _json(acquisition_service.rounds(offset=offset, limit=limit))

    @app.post("/api/rounds")
    def acquisition_round_create(body: RoundCreateRequest) -> JSONResponse:
        try:
            return _json(
                acquisition_service.create_round(
                    pool_id=body.pool_id,
                    device_seeds=body.device_seeds,
                    starts_at_ms=body.starts_at_ms,
                    min_inter_device_gap_ms=body.min_inter_device_gap_ms,
                    min_repeat_gap_ms=body.min_repeat_gap_ms,
                )
            )
        except AcquisitionNotFound:
            return _json({"error": "target pool not found"}, 404)
        except ValueError as error:
            return _json({"error": str(error)}, 409)

    @app.get("/api/operations")
    def acquisition_operations(
        round_id: str = Query(min_length=1, max_length=200),
    ) -> JSONResponse:
        try:
            return _json(acquisition_service.operations(round_id))
        except AcquisitionNotFound:
            return _json({"error": "round not found"}, 404)

    @app.get("/api/coverage")
    def acquisition_coverage(
        round_id: str = Query(min_length=1, max_length=200),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> JSONResponse:
        try:
            return _json(
                acquisition_service.coverage(round_id, offset=offset, limit=limit)
            )
        except AcquisitionNotFound:
            return _json({"error": "round not found"}, 404)

    def apply_operator_command(
        command_type: str, body: OperatorCommand
    ) -> JSONResponse:
        try:
            return _json(
                acquisition_service.apply_command(
                    command_type,
                    body.command_id,
                    body.scope,
                    body.scope_id,
                )
            )
        except AcquisitionNotFound:
            return _json({"error": "command target not found"}, 404)
        except AcquisitionConflict as error:
            return _json({"error": str(error)}, 409)

    @app.post("/api/commands/start")
    def acquisition_start(body: OperatorCommand) -> JSONResponse:
        return apply_operator_command("start", body)

    @app.post("/api/commands/pause")
    def acquisition_pause(body: OperatorCommand) -> JSONResponse:
        return apply_operator_command("pause", body)

    @app.post("/api/commands/stop")
    def acquisition_stop(body: OperatorCommand) -> JSONResponse:
        return apply_operator_command("stop", body)

    @app.post("/api/commands/retry")
    def acquisition_retry(body: RetryCommand) -> JSONResponse:
        try:
            return _json(acquisition_service.retry(body.command_id, body.assignment_id))
        except AcquisitionNotFound:
            return _json({"error": "assignment not found"}, 404)
        except AcquisitionConflict as error:
            return _json({"error": str(error)}, 409)

    @app.get("/api/diagnostics/{assignment_id}")
    def acquisition_diagnostics(
        assignment_id: int,
        limit: int = Query(default=20, ge=1, le=100),
    ) -> JSONResponse:
        if assignment_id <= 0:
            return _json({"error": "assignment not found"}, 404)
        try:
            return _json(acquisition_service.diagnostics(assignment_id, limit=limit))
        except AcquisitionNotFound:
            return _json({"error": "assignment not found"}, 404)

    @app.get("/api/diagnostic-screenshots/{screenshot_id}")
    def acquisition_diagnostic_screenshot(screenshot_id: str) -> Response:
        try:
            path, media_type = acquisition_service.diagnostic_screenshot(screenshot_id)
        except AcquisitionNotFound:
            return _json({"error": "diagnostic screenshot not found"}, 404)
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def operator_account(account_id: str) -> WebAccount:
        if registry is None:
            raise KeyError(account_id)
        return runtime_account(registry.by_account_id(account_id))

    def account_readiness(account: WebAccount) -> dict[str, object]:
        account = runtime_account(account)
        settings = database.account_operator_settings(
            account.account_id,
            default_ai_enabled=(account.enabled and account.browser_dm_enabled),
            default_followback_enabled=(
                account.enabled and account.browser_followback_enabled
            ),
        )
        runtime_model = runtime_settings.provider_credentials()
        circuit = database.browser_action_circuit(
            account.account_id, "followback", now_ms=int(clock() * 1_000)
        )
        return {
            "account_id": account.account_id,
            "device_id": account.device_id,
            "mode": account.mode,
            "enabled": account.enabled,
            "ai_enabled": bool(account.enabled and settings["ai_enabled"]),
            "followback_enabled": bool(
                account.enabled and settings["followback_enabled"]
            ),
            "followback_circuit_state": circuit["state"],
            "followback_circuit_reason": circuit["reason"],
            "followback_cooldown_until_ms": circuit["cooldown_until_ms"],
            "private_channel_configured": any(
                value.strip()
                for value in (
                    account.private_channel_hint,
                    account.whatsapp,
                    account.telegram,
                )
            ),
            "offer_configured": bool(account.offer_context.strip()),
            "faq_configured": bool(account.faq_text.strip()),
            "model_configured": all(
                bool(value and value.strip())
                for value in (
                    runtime_model.base_url,
                    runtime_model.api_key,
                    runtime_model.model,
                )
            ),
        }

    def redact_destination(value: object, account_id: str) -> object:
        try:
            account = operator_account(account_id)
            destinations = tuple(
                " ".join(value.split())
                for value in (
                    account.private_channel_hint,
                    account.whatsapp,
                    account.telegram,
                )
                if value.strip()
            )
        except KeyError:
            destinations = ()
        if not destinations:
            return value
        destination_patterns = tuple(
            re.compile(
                r"\s+".join(re.escape(token) for token in destination.split()),
                re.IGNORECASE,
            )
            for destination in destinations
        )

        def redact(item: object) -> object:
            if isinstance(item, str):
                for pattern in destination_patterns:
                    item = pattern.sub("[private channel configured]", item)
                return item
            if isinstance(item, list):
                return [redact(child) for child in item]
            if isinstance(item, dict):
                return {key: redact(child) for key, child in item.items()}
            return item

        return redact(value)

    def provider_payload() -> dict[str, object]:
        provider = runtime_settings.provider_credentials()
        return {
            "base_url": provider.base_url,
            "model": provider.model,
            "key_configured": provider.key_configured,
        }

    def validate_provider_url(base_url: str) -> bool:
        parsed = urlparse(base_url)
        if parsed.scheme == "https" and bool(parsed.hostname):
            return True
        return parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }

    @app.get("/api/settings")
    def settings_snapshot() -> JSONResponse:
        accounts = []
        for account in () if registry is None else registry.accounts:
            configured = runtime_settings.account_settings(account.account_id)
            accounts.append(
                {
                    "account_id": account.account_id,
                    "browser_profile_label": account.browser_profile_label,
                    "expected_tiktok_username": account.expected_tiktok_username,
                    **configured.__dict__,
                }
            )
        return _json({"provider": provider_payload(), "accounts": accounts})

    @app.post("/api/settings/provider")
    def save_provider_settings(body: ProviderSettingsCommand) -> JSONResponse:
        if not validate_provider_url(body.base_url):
            return _json({"error": "provider base URL must use HTTPS"}, 422)
        runtime_settings.save_provider(
            base_url=body.base_url,
            api_key=body.api_key,
            model=body.model,
            clear_key=body.clear_key,
        )
        return _json(provider_payload())

    @app.post("/api/settings/provider/test")
    def test_provider_settings() -> JSONResponse:
        provider = runtime_settings.provider_credentials()
        ok, elapsed_ms = provider_tester(provider)
        return _json(
            {
                "ok": bool(ok),
                "model": provider.model,
                "elapsed_ms": max(0, int(elapsed_ms)),
            }
        )

    @app.post("/api/settings/accounts/{account_id}")
    def save_account_settings(
        account_id: str, body: AccountAutomationSettingsCommand
    ) -> JSONResponse:
        try:
            account = operator_account(account_id)
        except KeyError:
            return _json({"error": "web account not found"}, 404)
        saved = runtime_settings.save_account(
            account.account_id,
            AccountRuntimeSettings(**body.model_dump()),
        )
        return _json({"account_id": account.account_id, **saved.__dict__})

    @app.get("/api/leads")
    def lead_inbox(
        limit: int = Query(default=20, ge=1, le=100),
        account_id: str | None = Query(default=None, min_length=1, max_length=200),
        conversation_id: str | None = Query(default=None, min_length=1, max_length=200),
        history_limit: int = Query(default=20, ge=1, le=100),
        inbound_fingerprint: str = Query(default="", max_length=200),
    ) -> JSONResponse:
        if (account_id is None) != (conversation_id is None):
            return _json(
                {"error": "account_id and conversation_id must be selected together"},
                400,
            )
        selected: dict[str, object] | None = None
        if account_id is not None and conversation_id is not None:
            try:
                operator_account(account_id)
                selected = database.selected_lead(
                    account_id,
                    conversation_id,
                    history_limit=history_limit,
                    inbound_fingerprint=inbound_fingerprint,
                )
            except KeyError:
                return _json({"error": "lead conversation not found"}, 404)
        accounts = (
            []
            if registry is None
            else [account_readiness(account) for account in registry.accounts]
        )
        browser_health = merge_browser_health_rows(
            () if registry is None else registry.accounts,
            database.browser_health_snapshot(),
            now_ms=int(clock() * 1_000),
        )
        account_ids = (
            ()
            if registry is None
            else tuple(account.account_id for account in registry.accounts)
        )
        conversations = database.lead_conversations(
            account_ids=account_ids,
            limit=limit,
            now_ms=int(clock() * 1_000),
        )
        conversations = [
            redact_destination(item, str(item["account_id"])) for item in conversations
        ]
        if selected is not None and account_id is not None:
            selected = redact_destination(selected, account_id)
        return _json(
            {
                "configured": registry is not None,
                "accounts": accounts,
                "conversations": conversations,
                "selected": selected,
                "funnel": database.lead_funnel_snapshot(account_ids=account_ids),
                "sales": database.lead_sales_snapshot(account_ids=account_ids),
                "browser_health": browser_health,
            }
        )

    @app.post("/api/leads/{account_id}/{conversation_id}/takeover")
    def lead_takeover(
        account_id: str, conversation_id: str, body: LeadTakeoverCommand
    ) -> JSONResponse:
        try:
            operator_account(account_id)
            return _json(
                database.takeover_lead(
                    account_id,
                    conversation_id,
                    body.command_id,
                    reason=body.reason,
                    occurred_at_ms=int(clock() * 1_000),
                )
            )
        except KeyError:
            return _json({"error": "lead conversation not found"}, 404)
        except OperatorCommandConflict as error:
            return _json({"error": str(error)}, 409)

    @app.post("/api/leads/{account_id}/{conversation_id}/return-to-ai")
    def lead_return_to_ai(
        account_id: str, conversation_id: str, body: LeadCommand
    ) -> JSONResponse:
        try:
            account = operator_account(account_id)
            settings = database.account_operator_settings(
                account.account_id,
                default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                default_followback_enabled=(
                    account.enabled and account.browser_followback_enabled
                ),
            )
            return _json(
                database.return_lead_to_ai(
                    account_id,
                    conversation_id,
                    body.command_id,
                    account_ai_enabled=bool(account.enabled and settings["ai_enabled"]),
                )
            )
        except KeyError:
            return _json({"error": "lead conversation not found"}, 404)
        except ValueError as error:
            return _json({"error": str(error)}, 409)

    @app.post("/api/leads/{account_id}/{conversation_id}/manual-reply-plan")
    def lead_manual_reply_plan(
        account_id: str, conversation_id: str, body: ManualReplyPlanCommand
    ) -> JSONResponse:
        try:
            operator_account(account_id)
            return _json(
                database.create_manual_reply_plan(
                    account_id,
                    conversation_id,
                    body.command_id,
                    inbound_fingerprint=body.inbound_fingerprint,
                    reply_text=body.reply_text,
                    now_ms=int(clock() * 1_000),
                )
            )
        except KeyError:
            return _json({"error": "lead or selected inbound not found"}, 404)
        except ValueError as error:
            return _json({"error": str(error)}, 409)

    @app.post("/api/leads/{account_id}/{conversation_id}/sale")
    def lead_sale(
        account_id: str, conversation_id: str, body: LeadSaleCommand
    ) -> JSONResponse:
        try:
            operator_account(account_id)
            return _json(
                database.record_lead_sale_command(
                    account_id,
                    conversation_id,
                    body.command_id,
                    amount_minor=body.amount_minor,
                    currency=body.currency,
                    status=body.status,
                    occurred_at_ms=body.occurred_at_ms,
                )
            )
        except KeyError:
            return _json({"error": "lead conversation not found"}, 404)
        except OperatorCommandConflict as error:
            return _json({"error": str(error)}, 409)
        except ValueError as error:
            return _json({"error": str(error)}, 400)

    def update_account_setting(
        account_id: str,
        body: AccountEnableCommand,
        *,
        setting: str,
    ) -> JSONResponse:
        try:
            account = operator_account(account_id)
        except KeyError:
            return _json({"error": "web account not found"}, 404)
        if setting == "followback" and body.enabled:
            circuit = database.browser_action_circuit(
                account_id, "followback", now_ms=int(clock() * 1_000)
            )
            if circuit["state"] == "cooldown":
                return _json(
                    {
                        "error": "followback_cooldown_active",
                        "cooldown_until_ms": circuit["cooldown_until_ms"],
                    },
                    409,
                )
        try:
            return _json(
                database.set_account_operator_setting(
                    account_id,
                    body.command_id,
                    setting=setting,
                    enabled=body.enabled,
                    default_ai_enabled=(account.enabled and account.browser_dm_enabled),
                    default_followback_enabled=(
                        account.enabled and account.browser_followback_enabled
                    ),
                )
            )
        except OperatorCommandConflict as error:
            return _json({"error": str(error)}, 409)

    @app.post("/api/accounts/{account_id}/ai-enable")
    def account_ai_enable(account_id: str, body: AccountEnableCommand) -> JSONResponse:
        return update_account_setting(account_id, body, setting="ai")

    @app.post("/api/accounts/{account_id}/followback-enable")
    def account_followback_enable(
        account_id: str, body: AccountEnableCommand
    ) -> JSONResponse:
        return update_account_setting(account_id, body, setting="followback")

    @app.post("/api/accounts/{account_id}/followback-cooldown")
    def account_followback_cooldown(
        account_id: str, body: FollowbackCooldownCommand
    ) -> JSONResponse:
        try:
            operator_account(account_id)
            return _json(
                database.record_followback_cooldown_command(
                    account_id,
                    body.command_id,
                    reason=body.reason,
                    cooldown_seconds=body.cooldown_seconds,
                    now_ms=int(clock() * 1_000),
                )
            )
        except KeyError:
            return _json({"error": "web account not found"}, 404)
        except OperatorCommandConflict as error:
            return _json({"error": str(error)}, 409)
        except ValueError as error:
            return _json({"error": str(error)}, 400)

    static = Path(__file__).parent / "static"
    console = static / "console"

    def console_index() -> FileResponse:
        response = FileResponse(console / "index.html", media_type="text/html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    @app.get("/operations")
    @app.get("/inbox")
    @app.get("/analytics")
    @app.get("/settings")
    def operator_console() -> FileResponse:
        return console_index()

    @app.get("/console-assets/{asset_name:path}")
    def console_asset(asset_name: str) -> Response:
        if _CONSOLE_ASSET.fullmatch(asset_name) is None:
            return _json({"detail": "Not Found"}, 404)
        asset_path = console / asset_name
        if not asset_path.is_file():
            return _json({"detail": "Not Found"}, 404)
        response = FileResponse(asset_path)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/dashboard.css")
    def dashboard_css() -> FileResponse:
        return FileResponse(static / "dashboard.css", media_type="text/css")

    @app.get("/dashboard.js")
    def dashboard_js() -> FileResponse:
        return FileResponse(static / "dashboard.js", media_type="text/javascript")

    return app

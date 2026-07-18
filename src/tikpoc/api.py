import json
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .acquisition_db import AcquisitionRepository
from .acquisition_service import (
    AcquisitionConflict,
    AcquisitionNotFound,
    AcquisitionService,
)
from .api_models import (
    AccountEnableCommand,
    BrowserActionClaimRequest,
    BrowserActionResultRequest,
    BrowserEventRequest,
    BrowserHealthRequest,
    BrowserIdentityRequest,
    BrowserReplyPlanRequest,
    BrowserReplyResultRequest,
    DeviceEventRequest,
    LeadCommand,
    LeadSaleCommand,
    LeadTakeoverCommand,
    ManualReplyPlanCommand,
    OperatorCommand,
    PoolImportRequest,
    RetryCommand,
    RoundCreateRequest,
)
from .browser_dm import BrowserConversationBusy, BrowserDmService, BrowserInbound
from .db import Database, OperatorCommandConflict
from .messaging import AiReplyClient
from .web_accounts import WebAccount, WebAccountRegistry
from .webhooks import (
    WebhookPayloadError,
    WebhookSignatureError,
    parse_business_message_webhook,
    verify_tiktok_signature,
)


_BROWSER_PATHS = {
    "/api/browser-events",
    "/api/browser-dm/reply-plan",
    "/api/browser-dm/reply-result",
    "/api/browser-actions/claim",
    "/api/browser-actions/result",
    "/api/browser-health",
}
_EXTENSION_ORIGIN = re.compile(r"chrome-extension://[a-p]{32}")


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
    return account


def create_app(
    database_path: Path,
    *,
    registry: WebAccountRegistry | None = None,
    browser_dm_service: BrowserDmService | None = None,
    browser_extension_origins: Iterable[str] | None = None,
    tiktok_app_secret: str = "",
    webhook_max_age_seconds: int = 300,
    clock: Callable[[], float] = time.time,
    import_roots: Iterable[Path] | None = None,
) -> FastAPI:
    database = Database(database_path)
    database.migrate()
    acquisition = AcquisitionRepository(database_path)
    acquisition.migrate()
    acquisition_service = AcquisitionService(
        acquisition,
        clock_ms=lambda: int(clock() * 1000),
        import_roots=tuple(import_roots or (database_path.parent,)),
    )
    acquisition_service.migrate()
    if browser_dm_service is None and registry is not None:
        browser_dm_service = BrowserDmService(
            database,
            registry,
            AiReplyClient.from_environment(),
        )
    if browser_extension_origins is None:
        browser_extension_origins = os.getenv(
            "TIKPOC_BROWSER_EXTENSION_ORIGINS", ""
        ).split(",")
    browser_origins = {
        "https://tiktok.com",
        "https://www.tiktok.com",
        *(
            origin.strip()
            for origin in browser_extension_origins
            if _EXTENSION_ORIGIN.fullmatch(origin.strip())
        ),
    }

    app = FastAPI(title="TikPoc Operator API", docs_url=None, redoc_url=None)
    app.state.database = database
    app.state.acquisition = acquisition
    app.state.acquisition_service = acquisition_service
    app.state.registry = registry
    app.state.browser_dm_service = browser_dm_service
    app.state.browser_origins = browser_origins
    app.state.tiktok_app_secret = tiktok_app_secret
    app.state.webhook_max_age_seconds = webhook_max_age_seconds
    app.state.clock = clock

    @app.middleware("http")
    async def browser_request_gate(request: Request, call_next: Callable):
        path = request.url.path
        if path not in _BROWSER_PATHS:
            return await call_next(request)
        origin = request.headers.get("origin")
        allowed_origin = origin if origin in browser_origins else None
        if request.method == "OPTIONS":
            if allowed_origin is None:
                return Response(status_code=404)
            response: Response = Response(status_code=204)
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        elif request.method == "POST":
            if allowed_origin is None:
                return _json({"error": "browser origin is not allowed"}, 403)
            media_type = request.headers.get("content-type", "").split(";", 1)[0]
            if media_type.strip().lower() != "application/json":
                response = _json(
                    {"error": "browser request must use application/json"}, 415
                )
            else:
                response = await call_next(request)
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

    @app.post("/api/browser-events")
    async def browser_event(request: Request) -> JSONResponse:
        try:
            body = BrowserEventRequest.model_validate(await _json_object(request))
            _browser_account(registry, body)
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
        except KeyError:
            return _json({"error": "browser reply plan not found"}, 404)
        except (TypeError, ValueError, ValidationError):
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
                claimed = database.claim_browser_action(
                    body.account_id,
                    body.action_type,
                    body.action_key,
                    body.owner_id,
                    body.timestamp_ms,
                    body.lease_seconds,
                )
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
            recorded = database.finish_browser_action(
                body.account_id,
                body.action_type,
                body.action_key,
                body.owner_id,
                body.state,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        return _json({"recorded": recorded})

    @app.post("/api/browser-health")
    async def browser_health(request: Request) -> JSONResponse:
        try:
            body = BrowserHealthRequest.model_validate(await _json_object(request))
            _browser_account(registry, body)
        except (KeyError, TypeError, ValueError, ValidationError):
            return _json({"error": "invalid browser request"}, 400)
        database.record_runtime_event(
            f"browser_health_{body.page_role}", body.account_id
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
        return registry.by_account_id(account_id)

    def account_readiness(account: WebAccount) -> dict[str, object]:
        settings = database.account_operator_settings(
            account.account_id,
            default_ai_enabled=(account.enabled and account.browser_dm_enabled),
            default_followback_enabled=(
                account.enabled and account.browser_followback_enabled
            ),
        )
        runtime_model = AiReplyClient.from_environment()
        return {
            "account_id": account.account_id,
            "device_id": account.device_id,
            "mode": account.mode,
            "enabled": account.enabled,
            "ai_enabled": bool(account.enabled and settings["ai_enabled"]),
            "followback_enabled": bool(
                account.enabled and settings["followback_enabled"]
            ),
            "private_channel_configured": bool(account.private_channel_hint.strip()),
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
            destination = " ".join(
                operator_account(account_id).private_channel_hint.split()
            )
        except KeyError:
            destination = ""
        if not destination:
            return value
        destination_pattern = re.compile(
            r"\s+".join(re.escape(token) for token in destination.split()),
            re.IGNORECASE,
        )

        def redact(item: object) -> object:
            if isinstance(item, str):
                return destination_pattern.sub("[private channel configured]", item)
            if isinstance(item, list):
                return [redact(child) for child in item]
            if isinstance(item, dict):
                return {key: redact(child) for key, child in item.items()}
            return item

        return redact(value)

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
        account_ids = (
            ()
            if registry is None
            else tuple(account.account_id for account in registry.accounts)
        )
        conversations = database.lead_conversations(
            account_ids=account_ids, limit=limit
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

    static = Path(__file__).parent / "static"

    @app.get("/")
    def dashboard_html() -> FileResponse:
        return FileResponse(static / "dashboard.html", media_type="text/html")

    @app.get("/dashboard.css")
    def dashboard_css() -> FileResponse:
        return FileResponse(static / "dashboard.css", media_type="text/css")

    @app.get("/dashboard.js")
    def dashboard_js() -> FileResponse:
        return FileResponse(static / "dashboard.js", media_type="text/javascript")

    return app

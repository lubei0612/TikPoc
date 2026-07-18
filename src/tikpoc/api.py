import json
import os
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from .acquisition_db import AcquisitionRepository
from .api_models import (
    BrowserActionClaimRequest,
    BrowserActionResultRequest,
    BrowserEventRequest,
    BrowserHealthRequest,
    BrowserIdentityRequest,
    BrowserReplyPlanRequest,
    BrowserReplyResultRequest,
    DeviceEventRequest,
)
from .browser_dm import BrowserConversationBusy, BrowserDmService, BrowserInbound
from .db import Database
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
) -> FastAPI:
    database = Database(database_path)
    database.migrate()
    acquisition = AcquisitionRepository(database_path)
    acquisition.migrate()
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
            _browser_account(registry, body)
            reply = browser_dm_service.plan(
                BrowserInbound(
                    account_id=body.account_id,
                    device_id=body.device_id,
                    conversation_id=body.conversation_id,
                    fingerprint=body.fingerprint,
                    participant_username=body.participant_username,
                    text=body.text,
                    timestamp_ms=body.timestamp_ms,
                )
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
            _browser_account(registry, body)
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

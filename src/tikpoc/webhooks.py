import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class WebhookSignatureError(ValueError):
    pass


class WebhookPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessMessageEvent:
    event_name: str
    business_id: str
    conversation_id: str
    message_id: str
    sender_id: str
    sender_username: str
    text: str
    message_type: str
    timestamp_ms: int
    is_follower: bool | None
    source: str
    content: dict[str, object]


def verify_tiktok_signature(
    body: bytes,
    signature_header: str,
    app_secret: str,
    *,
    now: int | None = None,
    max_age_seconds: int = 300,
) -> int:
    try:
        parts = dict(
            part.strip().split("=", 1)
            for part in signature_header.split(",")
            if part.strip()
        )
        timestamp_text = parts["t"]
        received_signature = parts["s"]
        timestamp = int(timestamp_text)
    except (KeyError, TypeError, ValueError) as error:
        raise WebhookSignatureError("malformed signature header") from error

    signed_payload = timestamp_text.encode("ascii") + b"." + body
    expected_signature = hmac.new(
        app_secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, received_signature):
        raise WebhookSignatureError("signature mismatch")

    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > max(0, int(max_age_seconds)):
        raise WebhookSignatureError("timestamp outside tolerance")
    return timestamp


def parse_business_message_webhook(body: bytes) -> BusinessMessageEvent | None:
    try:
        envelope = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise WebhookPayloadError("invalid webhook JSON") from error
    if not isinstance(envelope, dict):
        raise WebhookPayloadError("webhook payload must be an object")

    event_name = str(envelope.get("event") or "")
    if event_name not in {"im_receive_msg", "im_receive_msg_eu"}:
        return None

    raw_content = envelope.get("content")
    try:
        content = json.loads(raw_content)
    except (json.JSONDecodeError, TypeError) as error:
        raise WebhookPayloadError("invalid webhook content JSON") from error
    if not isinstance(content, dict):
        raise WebhookPayloadError("webhook content must be an object")

    business_id = str(envelope.get("user_openid") or "")
    conversation_id = str(content.get("conversation_id") or "")
    message_id = str(content.get("message_id") or "")
    if not business_id or not conversation_id or not message_id:
        raise WebhookPayloadError("inbound message identity is incomplete")

    from_user = content.get("from_user")
    sender_from_object = (
        str(from_user.get("id") or "") if isinstance(from_user, dict) else ""
    )
    sender_id = str(content.get("unique_identifier") or sender_from_object)
    text_object = content.get("text")
    text = str(text_object.get("body") or "") if isinstance(text_object, dict) else ""
    message_tag = content.get("message_tag")
    source = str(message_tag.get("source") or "") if isinstance(message_tag, dict) else ""
    follower = content.get("is_follower")
    is_follower = follower if isinstance(follower, bool) else None

    try:
        timestamp_ms = int(content.get("timestamp") or 0)
    except (TypeError, ValueError) as error:
        raise WebhookPayloadError("invalid message timestamp") from error

    return BusinessMessageEvent(
        event_name=event_name,
        business_id=business_id,
        conversation_id=conversation_id,
        message_id=message_id,
        sender_id=sender_id,
        sender_username=str(content.get("from") or ""),
        text=text,
        message_type=str(content.get("type") or "OTHER").upper(),
        timestamp_ms=timestamp_ms,
        is_follower=is_follower,
        source=source,
        content=dict(content),
    )

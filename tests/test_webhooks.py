import hashlib
import hmac
import json

import pytest

from tikpoc.webhooks import (
    WebhookPayloadError,
    WebhookSignatureError,
    parse_business_message_webhook,
    verify_tiktok_signature,
)


def _signature(body: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        str(timestamp).encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},s={digest}"


def test_verify_tiktok_signature_uses_exact_request_bytes() -> None:
    body = b'{"event": "im_receive_msg", "content": "{}"}'
    header = _signature(body, "app-secret", 1_720_000_000)

    timestamp = verify_tiktok_signature(
        body,
        header,
        "app-secret",
        now=1_720_000_020,
        max_age_seconds=60,
    )

    assert timestamp == 1_720_000_000


def test_verify_tiktok_signature_rejects_invalid_signature() -> None:
    body = b'{"event":"im_receive_msg"}'

    with pytest.raises(WebhookSignatureError, match="signature mismatch"):
        verify_tiktok_signature(
            body,
            "t=1720000000,s=bad",
            "app-secret",
            now=1_720_000_020,
        )


def test_verify_tiktok_signature_rejects_stale_or_future_timestamp() -> None:
    body = b"{}"

    with pytest.raises(WebhookSignatureError, match="timestamp outside tolerance"):
        verify_tiktok_signature(
            body,
            _signature(body, "app-secret", 1_720_000_000),
            "app-secret",
            now=1_720_000_301,
            max_age_seconds=300,
        )

    with pytest.raises(WebhookSignatureError, match="timestamp outside tolerance"):
        verify_tiktok_signature(
            body,
            _signature(body, "app-secret", 1_720_000_400),
            "app-secret",
            now=1_720_000_000,
            max_age_seconds=300,
        )


def test_verify_tiktok_signature_rejects_malformed_header() -> None:
    with pytest.raises(WebhookSignatureError, match="malformed signature header"):
        verify_tiktok_signature(b"{}", "not-a-signature", "app-secret", now=1)


def test_parse_business_message_webhook_extracts_inbound_text() -> None:
    content = {
        "from": "prospect_user",
        "to": "business_user",
        "unique_identifier": "person-123",
        "from_user": {"id": "person-123", "role": "personal_account"},
        "to_user": {"id": "business-456", "role": "business_account"},
        "conversation_id": "conversation+abc==",
        "message_id": "message-789",
        "timestamp": 1_720_000_000_123,
        "type": "text",
        "text": {"body": "Hello, can you tell me more?"},
        "is_follower": True,
        "message_tag": {"source": "WEB"},
    }
    body = json.dumps(
        {
            "client_key": "client-key",
            "event": "im_receive_msg",
            "create_time": 1_720_000_000,
            "user_openid": "business-456",
            "content": json.dumps(content, separators=(",", ":")),
        },
        separators=(",", ":"),
    ).encode("utf-8")

    event = parse_business_message_webhook(body)

    assert event is not None
    assert event.event_name == "im_receive_msg"
    assert event.business_id == "business-456"
    assert event.conversation_id == "conversation+abc=="
    assert event.message_id == "message-789"
    assert event.sender_id == "person-123"
    assert event.sender_username == "prospect_user"
    assert event.text == "Hello, can you tell me more?"
    assert event.message_type == "TEXT"
    assert event.is_follower is True
    assert event.source == "WEB"


def test_parse_business_message_webhook_accepts_eu_event_without_body() -> None:
    body = json.dumps(
        {
            "event": "im_receive_msg_eu",
            "user_openid": "business-456",
            "content": json.dumps(
                {
                    "from": "eu_user",
                    "unique_identifier": "person-eu",
                    "conversation_id": "conversation-eu",
                    "message_id": "message-eu",
                    "timestamp": 1_720_000_000_456,
                    "type": "other",
                }
            ),
        }
    ).encode("utf-8")

    event = parse_business_message_webhook(body)

    assert event is not None
    assert event.event_name == "im_receive_msg_eu"
    assert event.text == ""
    assert event.message_type == "OTHER"


def test_parse_business_message_webhook_ignores_non_inbound_event() -> None:
    body = json.dumps(
        {"event": "im_send_msg", "user_openid": "business-456", "content": "{}"}
    ).encode("utf-8")

    assert parse_business_message_webhook(body) is None


@pytest.mark.parametrize(
    "body",
    [b"not-json", b'{"event":"im_receive_msg","content":"not-json"}'],
)
def test_parse_business_message_webhook_rejects_malformed_payload(body: bytes) -> None:
    with pytest.raises(WebhookPayloadError):
        parse_business_message_webhook(body)

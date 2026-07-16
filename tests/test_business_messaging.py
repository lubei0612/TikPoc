import json

import pytest

from tikpoc.business_messaging import (
    BusinessMessagingClient,
    BusinessMessagingError,
    BusinessToken,
)


class MemoryTokenStore:
    def __init__(self, token: BusinessToken) -> None:
        self.token = token
        self.saved: list[BusinessToken] = []

    def load(self) -> BusinessToken:
        return self.token

    def save(self, token: BusinessToken) -> None:
        self.token = token
        self.saved.append(token)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _token(*, access_expires_at: int = 10_000) -> BusinessToken:
    return BusinessToken(
        client_id="client-id",
        client_secret="client-secret",
        access_token="access-token",
        refresh_token="refresh-token",
        access_expires_at=access_expires_at,
        refresh_expires_at=100_000,
        business_id="business-01",
    )


def test_business_messaging_client_sends_text_to_conversation() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            {"code": 0, "message": "OK", "data": {"message": {"message_id": "out-1"}}}
        )

    client = BusinessMessagingClient(
        MemoryTokenStore(_token()), opener=opener, clock=lambda: 1_000
    )

    message_id = client.send_text("conversation+abc==", "Hello there")

    assert message_id == "out-1"
    request, timeout = requests[0]
    assert request.full_url.endswith("/open_api/v1.3/business/message/send/")
    assert request.get_header("Access-token") == "access-token"
    assert timeout == 30
    body = json.loads(request.data)
    assert body == {
        "business_id": "business-01",
        "recipient_type": "CONVERSATION",
        "recipient": "conversation+abc==",
        "message_type": "TEXT",
        "text": {"body": "Hello there"},
    }


@pytest.mark.parametrize("action", ["TYPING", "MARK_READ"])
def test_business_messaging_client_sends_sender_action(action: str) -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(
            {"code": 0, "message": "OK", "data": {"message": {"message_id": ""}}}
        )

    client = BusinessMessagingClient(
        MemoryTokenStore(_token()), opener=opener, clock=lambda: 1_000
    )

    client.send_sender_action("conversation-1", action)

    body = json.loads(requests[0].data)
    assert body["message_type"] == "SENDER_ACTION"
    assert body["sender_action"] == action


def test_business_messaging_client_refreshes_expiring_token_before_send() -> None:
    requests = []
    store = MemoryTokenStore(_token(access_expires_at=1_030))

    def opener(request, timeout):
        requests.append(request)
        if request.full_url.endswith("/tt_user/oauth2/refresh_token/"):
            return FakeResponse(
                {
                    "code": 0,
                    "message": "OK",
                    "data": {
                        "access_token": "new-access",
                        "refresh_token": "new-refresh",
                        "expires_in": 86_400,
                        "refresh_token_expires_in": 31_536_000,
                        "open_id": "business-01",
                    },
                }
            )
        return FakeResponse(
            {"code": 0, "message": "OK", "data": {"message": {"message_id": "out-2"}}}
        )

    client = BusinessMessagingClient(store, opener=opener, clock=lambda: 1_000)

    assert client.send_text("conversation-1", "Hi") == "out-2"
    assert requests[0].full_url.endswith("/tt_user/oauth2/refresh_token/")
    assert requests[1].get_header("Access-token") == "new-access"
    assert store.saved[0].access_token == "new-access"
    assert store.saved[0].access_expires_at == 87_400


def test_business_messaging_client_does_not_confirm_missing_message_id() -> None:
    def opener(request, timeout):
        return FakeResponse({"code": 0, "message": "OK", "data": {"message": {}}})

    client = BusinessMessagingClient(
        MemoryTokenStore(_token()), opener=opener, clock=lambda: 1_000
    )

    with pytest.raises(BusinessMessagingError, match="message result is uncertain"):
        client.send_text("conversation-1", "Hi")


def test_business_messaging_client_rejects_empty_text() -> None:
    client = BusinessMessagingClient(
        MemoryTokenStore(_token()), opener=lambda request, timeout: None
    )

    with pytest.raises(ValueError, match="message text is empty"):
        client.send_text("conversation-1", "   ")

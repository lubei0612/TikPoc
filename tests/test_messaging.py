import json

import pytest

from tikpoc.messaging import AiReplyClient, RuntimeAiReplyClient
from tikpoc.runtime_settings import RuntimeSettingsStore


class FakeResponse:
    def __init__(self, content: object = "Thanks, how can I help?") -> None:
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": self.content}}]}
        ).encode()


class RawResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class ReadErrorResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        raise OSError("response interrupted")


def test_ai_reply_uses_openai_compatible_chat_endpoint() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )

    reply = client.reply("How much is it?")

    assert reply == "Thanks, how can I help?"
    request, timeout = requests[0]
    assert request.full_url == "https://llm.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert timeout == 30


def test_ai_reply_falls_back_when_not_configured() -> None:
    client = AiReplyClient(base_url="", api_key="", model="")

    assert client.reply("Hello") == "Thanks for your message. How can I help?"


def test_runtime_ai_client_loads_latest_provider_for_each_request(tmp_path) -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse()

    store = RuntimeSettingsStore(tmp_path / "operator-settings.json")
    client = RuntimeAiReplyClient(store.provider_credentials, opener=opener)
    assert client.reply("Hello") == "Thanks for your message. How can I help?"

    store.save_provider(
        base_url="https://provider.example/v1",
        api_key="synthetic-secret",
        model="model-b",
    )

    assert client.reply("Hello") == "Thanks, how can I help?"
    assert requests[0].full_url == "https://provider.example/v1/chat/completions"


def test_lead_reply_prompt_contains_offer_faq_stage_and_invite() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse("Happy to help. WhatsApp: +1 555 0100")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )

    client.reply_conversation(
        [{"direction": "inbound", "text": "Do you have black bags?"}],
        private_channel_hint="WhatsApp: +1 555 0100",
        offer_context="Black bags are available in the current catalog.",
        faq_context="Shipping usually takes 5-7 days.",
        conversation_stage="qualified",
        should_invite=True,
    )

    system = json.loads(requests[0].data)["messages"][0]["content"]
    assert "Conversation stage: qualified" in system
    assert "Black bags are available in the current catalog." in system
    assert "Shipping usually takes 5-7 days." in system
    assert "WhatsApp: +1 555 0100" in system


def test_lead_reply_prompt_bounds_context_and_excludes_private_values() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse()

    api_key = "PRIVATE_API_KEY"
    fallback = "PRIVATE_ACCOUNT_FALLBACK"
    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key=api_key,
        model="reply-model",
        opener=opener,
    )

    client.reply_conversation(
        [{"direction": "inbound", "text": "What is available?"}],
        private_channel_hint="SECRET_DESTINATION",
        offer_context="Catalog offer " + "O" * 10_000,
        faq_context="Shipping FAQ " + "F" * 10_000,
        conversation_stage="qualified" + "S" * 1_000,
        should_invite=False,
        fallback=fallback,
    )

    system = json.loads(requests[0].data)["messages"][0]["content"]
    assert "Catalog offer" in system
    assert "Shipping FAQ" in system
    assert len(system) <= 8_000
    assert "SECRET_DESTINATION" not in system
    assert api_key not in system
    assert fallback not in system


def test_reply_conversation_uses_per_call_fallback_when_not_configured() -> None:
    client = AiReplyClient(
        base_url="",
        api_key="",
        model="",
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


@pytest.mark.parametrize("fallback", ["", "   "])
def test_reply_conversation_uses_client_fallback_for_blank_per_call_value_when_not_configured(
    fallback: str,
) -> None:
    client = AiReplyClient(
        base_url="",
        api_key="",
        model="",
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback=fallback,
        max_characters=6,
    )

    assert reply == "Client"


def test_reply_conversation_uses_per_call_fallback_for_provider_error() -> None:
    def opener(request, timeout):
        raise OSError("provider unavailable")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


@pytest.mark.parametrize("fallback", ["", "   "])
def test_reply_conversation_uses_client_fallback_for_blank_per_call_value_after_provider_error(
    fallback: str,
) -> None:
    def opener(request, timeout):
        raise OSError("provider unavailable")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback=fallback,
        max_characters=6,
    )

    assert reply == "Client"


def test_reply_conversation_uses_builtin_fallback_for_blank_values() -> None:
    client = AiReplyClient(
        base_url="",
        api_key="",
        model="",
        fallback="   ",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="",
        max_characters=10,
    )

    assert reply == "Thanks for"


@pytest.mark.parametrize("exception_type", [TypeError, ValueError])
def test_reply_conversation_propagates_opener_programming_errors(
    exception_type: type[Exception],
) -> None:
    def opener(request, timeout):
        raise exception_type("broken opener")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )

    with pytest.raises(exception_type, match="broken opener"):
        client.reply_conversation(
            [{"direction": "inbound", "text": "Hello"}],
        )


def test_reply_conversation_uses_fallback_for_response_read_error() -> None:
    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=lambda request, timeout: ReadErrorResponse(),
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


@pytest.mark.parametrize("content", ["", "   ", None, {"text": "unexpected"}])
def test_reply_conversation_uses_per_call_fallback_for_invalid_content(
    content: object,
) -> None:
    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=lambda request, timeout: FakeResponse(content),
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


def test_reply_conversation_uses_fallback_for_malformed_provider_url() -> None:
    client = AiReplyClient(
        base_url="not-a-url",
        api_key="secret",
        model="reply-model",
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


@pytest.mark.parametrize(
    "body",
    [
        b"{malformed-json",
        b"\xff",
        json.dumps({}).encode(),
        json.dumps({"choices": [{}]}).encode(),
        json.dumps({"choices": [{"message": {}}]}).encode(),
    ],
)
def test_reply_conversation_uses_fallback_for_malformed_provider_response(
    body: bytes,
) -> None:
    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=lambda request, timeout: RawResponse(body),
        fallback="Client fallback",
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        fallback="Account fallback",
        max_characters=7,
    )

    assert reply == "Account"


def test_ai_reply_uses_bounded_conversation_history_and_handoff_hint() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse()

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )
    history = [
        {
            "direction": "inbound" if index % 2 == 0 else "outbound",
            "text": f"message-{index}",
        }
        for index in range(30)
    ]

    client.reply_conversation(
        history,
        private_channel_hint="Continue on WhatsApp: example",
        should_invite=True,
        max_history_messages=6,
    )

    payload = json.loads(requests[0].data)
    messages = payload["messages"]
    assert "Continue on WhatsApp: example" in messages[0]["content"]
    assert [message["content"] for message in messages[1:]] == [
        "message-24",
        "message-25",
        "message-26",
        "message-27",
        "message-28",
        "message-29",
    ]
    assert [message["role"] for message in messages[1:]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_ai_reply_asks_channel_preference_without_disclosing_destinations() -> None:
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse("Which channel do you prefer?")

    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=opener,
    )

    client.reply_conversation(
        [{"direction": "inbound", "text": "I want to order"}],
        private_channel_hint="CONTACT_A CHANNEL_A",
        ask_private_channel_preference=True,
        reply_tone="Brief and practical",
    )

    system = json.loads(requests[0].data)["messages"][0]["content"]
    assert "Ask whether the sender prefers WhatsApp or Telegram" in system
    assert "Brief and practical" in system
    assert "CONTACT_A" not in system
    assert "CHANNEL_A" not in system


def test_reply_conversation_preserves_max_character_limit() -> None:
    client = AiReplyClient(
        base_url="https://llm.example/v1",
        api_key="secret",
        model="reply-model",
        opener=lambda request, timeout: FakeResponse("A reply that is too long"),
    )

    reply = client.reply_conversation(
        [{"direction": "inbound", "text": "Hello"}],
        max_characters=7,
    )

    assert reply == "A reply"

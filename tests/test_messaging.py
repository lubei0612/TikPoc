import json

import pytest

from tikpoc.messaging import AiReplyClient


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
    )

    assert reply == "Account fallback"


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
    )

    assert reply == "Account fallback"


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
    )

    assert reply == "Account fallback"


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

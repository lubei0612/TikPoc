import json

from tikpoc.messaging import AiReplyClient


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {"choices": [{"message": {"content": "Thanks, how can I help?"}}]}
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

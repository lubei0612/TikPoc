import json
import os
from collections.abc import Callable
from urllib.request import Request, urlopen


class AiReplyClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        opener: Callable = urlopen,
        fallback: str = "Thanks for your message. How can I help?",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.opener = opener
        self.fallback = fallback

    @classmethod
    def from_environment(cls) -> "AiReplyClient":
        return cls(
            base_url=(
                os.getenv("TKAUTO_LLM_BASE_URL")
                or os.getenv("MODEL_MONITOR_LLM_BASE_URL")
                or ""
            ),
            api_key=(
                os.getenv("TKAUTO_LLM_API_KEY")
                or os.getenv("MODEL_MONITOR_LLM_API_KEY")
                or ""
            ),
            model=(
                os.getenv("TKAUTO_LLM_MODEL")
                or os.getenv("MODEL_MONITOR_LLM_MODEL")
                or ""
            ),
        )

    def reply(self, message: str) -> str:
        return self.reply_conversation(
            [{"direction": "inbound", "text": message[:500]}],
            max_history_messages=1,
            max_characters=160,
        )

    def reply_conversation(
        self,
        history: list[dict[str, object]],
        *,
        private_channel_hint: str = "",
        max_history_messages: int = 12,
        max_characters: int = 300,
    ) -> str:
        if not self.base_url or not self.api_key or not self.model:
            return self.fallback
        selected = history[-max(1, int(max_history_messages)) :]
        conversation: list[dict[str, str]] = []
        for item in selected:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if item.get("direction") == "outbound" else "user"
            conversation.append({"role": role, "content": text[:1000]})
        handoff = private_channel_hint.strip()
        handoff_instruction = (
            " When it is contextually useful, invite the sender to continue through "
            f"this private channel: {handoff}. Do not repeat the handoff if it already "
            "appears in the conversation, and never force it before answering the "
            "sender's question."
            if handoff
            else ""
        )
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "max_tokens": 220,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Reply to a TikTok direct message in the same language as "
                        "the sender. Be natural, concise, answer the actual question, "
                        "and do not invent prices, promises, links, or contact details."
                        + handoff_instruction
                    ),
                },
                *conversation,
            ],
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                data = json.loads(response.read())
            content = str(data["choices"][0]["message"]["content"]).strip()
            return content[: max(1, int(max_characters))] or self.fallback
        except (KeyError, IndexError, TypeError, ValueError, OSError):
            return self.fallback

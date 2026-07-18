import json
import os
from collections.abc import Callable
from urllib.request import Request, urlopen


_PROMPT_CONTEXT_LIMIT = 3_000
_PROMPT_DESTINATION_LIMIT = 500
_PROMPT_STAGE_LIMIT = 32
_SYSTEM_PROMPT_LIMIT = 8_000


def _bounded_prompt_fragment(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _build_system_prompt(
    *,
    offer_context: str,
    faq_context: str,
    conversation_stage: str,
    should_invite: bool,
    private_channel_hint: str,
) -> str:
    parts = [
        "Reply to the TikTok sender in the same language they use.",
        "Be natural and concise, answer the sender's actual question, and ask at "
        "most one qualifying question.",
        "Conversation stage: "
        + _bounded_prompt_fragment(conversation_stage, _PROMPT_STAGE_LIMIT),
        "Account offer facts: "
        + _bounded_prompt_fragment(offer_context, _PROMPT_CONTEXT_LIMIT),
        "FAQ facts: " + _bounded_prompt_fragment(faq_context, _PROMPT_CONTEXT_LIMIT),
        "Use only the supplied account offer and FAQ facts. Do not invent prices, "
        "inventory, delivery promises, discounts, payment instructions, refund "
        "decisions, links, or contact details.",
    ]
    if should_invite:
        destination = _bounded_prompt_fragment(
            private_channel_hint, _PROMPT_DESTINATION_LIMIT
        )
        if destination:
            parts.append(
                "Include one private-channel invitation using exactly this "
                f"destination: {destination}. Do not repeat it if it already appears "
                "in the conversation, and answer the sender's question first."
            )
    return "\n".join(parts)[:_SYSTEM_PROMPT_LIMIT]


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
        offer_context: str = "",
        faq_context: str = "",
        conversation_stage: str = "",
        should_invite: bool = False,
        fallback: str | None = None,
        max_history_messages: int = 12,
        max_characters: int = 300,
    ) -> str:
        effective_fallback = self.fallback if fallback is None else fallback
        if not self.base_url or not self.api_key or not self.model:
            return effective_fallback
        selected = history[-max(1, int(max_history_messages)) :]
        conversation: list[dict[str, str]] = []
        for item in selected:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if item.get("direction") == "outbound" else "user"
            conversation.append({"role": role, "content": text[:1000]})
        payload = {
            "model": self.model,
            "temperature": 0.4,
            "max_tokens": 220,
            "messages": [
                {
                    "role": "system",
                    "content": _build_system_prompt(
                        offer_context=offer_context,
                        faq_context=faq_context,
                        conversation_stage=conversation_stage,
                        should_invite=should_invite,
                        private_channel_hint=private_channel_hint,
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
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                return effective_fallback
            content = content.strip()
            return content[: max(1, int(max_characters))] or effective_fallback
        except (KeyError, IndexError, TypeError, ValueError, OSError):
            return effective_fallback

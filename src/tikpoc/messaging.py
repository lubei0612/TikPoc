import json
import os
from collections.abc import Callable
from urllib.request import Request, urlopen

from .runtime_settings import ProviderCredentials


_PROMPT_CONTEXT_LIMIT = 3_000
_PROMPT_DESTINATION_LIMIT = 500
_PROMPT_STAGE_LIMIT = 32
_SYSTEM_PROMPT_LIMIT = 8_000
_DEFAULT_FALLBACK = "Thanks for your message. How can I help?"


def _bounded_prompt_fragment(value: str, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalize_fallback(per_call: str | None, client: str) -> str:
    for candidate in (per_call, client):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return _DEFAULT_FALLBACK


def _build_system_prompt(
    *,
    offer_context: str,
    faq_context: str,
    conversation_stage: str,
    should_invite: bool,
    private_channel_hint: str,
    ask_private_channel_preference: bool,
    reply_tone: str,
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
    tone = _bounded_prompt_fragment(reply_tone, 500)
    if tone:
        parts.append(f"Account reply tone: {tone}")
    if ask_private_channel_preference:
        parts.append(
            "Ask whether the sender prefers WhatsApp or Telegram. Do not include "
            "either destination until the sender chooses one."
        )
    if should_invite:
        destination = _bounded_prompt_fragment(
            private_channel_hint, _PROMPT_DESTINATION_LIMIT
        )
        if destination:
            parts.append(
                "Include one private-channel invitation using exactly this "
                f"destination: {destination}. Do not repeat it if it already appears "
                "in the conversation, answer the sender's question first, and end "
                "with one short natural sentence inviting interested buyers to "
                "contact that destination for details or purchasing."
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
        fallback: str = _DEFAULT_FALLBACK,
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
        ask_private_channel_preference: bool = False,
        reply_tone: str = "",
        fallback: str | None = None,
        max_history_messages: int = 12,
        max_characters: int = 300,
    ) -> str:
        effective_fallback = _normalize_fallback(fallback, self.fallback)
        reply_limit = max(1, int(max_characters))
        bounded_fallback = effective_fallback[:reply_limit]
        if not (self.base_url and self.api_key and self.model):
            return bounded_fallback

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
                        ask_private_channel_preference=ask_private_channel_preference,
                        reply_tone=reply_tone,
                    ),
                },
                *conversation,
            ],
        }
        try:
            request = Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
        except (TypeError, ValueError):
            return bounded_fallback

        try:
            response_context = self.opener(request, timeout=30)
        except OSError:
            return bounded_fallback

        try:
            with response_context as response:
                body = response.read()
        except OSError:
            return bounded_fallback

        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            return bounded_fallback
        if isinstance(content, str) and content.strip():
            return content.strip()[:reply_limit]
        return bounded_fallback


class RuntimeAiReplyClient:
    def __init__(
        self,
        credentials_loader: Callable[[], ProviderCredentials],
        *,
        opener: Callable = urlopen,
        fallback: str = _DEFAULT_FALLBACK,
    ) -> None:
        self.credentials_loader = credentials_loader
        self.opener = opener
        self.fallback = fallback

    def _client(self) -> AiReplyClient:
        provider = self.credentials_loader()
        return AiReplyClient(
            base_url=provider.base_url,
            api_key=provider.api_key,
            model=provider.model,
            opener=self.opener,
            fallback=self.fallback,
        )

    def reply(self, message: str) -> str:
        return self._client().reply(message)

    def reply_conversation(
        self,
        history: list[dict[str, object]],
        **kwargs: object,
    ) -> str:
        return self._client().reply_conversation(history, **kwargs)

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


INVITE_COOLDOWN_MS = 24 * 60 * 60 * 1000
PROMPT_FRAGMENT_LIMIT = 3_000
PROMPT_DESTINATION_LIMIT = 500
PROMPT_LIMIT = 8_000


class ConversationStage(StrEnum):
    NEW = "new"
    ENGAGED = "engaged"
    QUALIFIED = "qualified"
    INVITED = "invited"
    CONTACT_CAPTURED = "contact_captured"
    HUMAN_REQUIRED = "human_required"
    CLOSED = "closed"


@dataclass(frozen=True)
class ConversionAssessment:
    stage: ConversationStage
    meaningful: bool
    should_invite: bool
    contact: str = ""
    human_reason: str = ""


_EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)(?![\w.-])",
    re.IGNORECASE,
)
_HANDLE_PATTERNS = (
    re.compile(
        r"(?:wechat(?:\s+id)?|微信(?:号)?|wx)\s*[:：]?\s*"
        r"([a-z][a-z0-9_.-]{4,31})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:telegram|tg)\s*[:：]?\s*(@[a-z0-9_]{5,32}|[a-z][a-z0-9_]{4,31})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:line(?:\s+id)?)\s*[:：]?\s*([a-z][a-z0-9_.-]{4,31})",
        re.IGNORECASE,
    ),
)
_LABELED_PHONE_PATTERN = re.compile(
    r"(?:whats?app|wa|phone|mobile|tel(?:ephone)?|电话|手机|手机号|联系号码)"
    r"\s*(?:number|号码)?\s*[:：]?\s*"
    r"(\+?\d[\d ()-]{5,24}\d)",
    re.IGNORECASE,
)
_PLAUSIBLE_PHONE_PATTERN = re.compile(r"(?<!\w)(\+?\d[\d ()-]{7,24}\d)(?!\w)")
_IDENTIFIER_CONTEXT_PATTERN = re.compile(
    r"(?:order|tracking|reference|invoice|product|item|id|code|price|"
    r"订单|单号|运单|物流|编号|货号)"
    r"(?:\s+(?:number|id))?\s*[:：#-]?\s*$",
    re.IGNORECASE,
)

_ACKNOWLEDGEMENTS = {
    "hello",
    "hellothere",
    "hey",
    "hi",
    "ok",
    "okay",
    "thankyou",
    "thanks",
    "thx",
    "你好",
    "您好",
    "好的",
    "嗯",
    "谢谢",
    "谢谢你",
    "收到",
}

_BUYING_PATTERNS = (
    re.compile(
        r"\b(product|item|price|pricing|cost|stock|availability|available|"
        r"ship|shipping|delivery|deliver|order|buy|purchase|catalog|size|colour|color)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"产品|商品|款式|价格|多少钱|有货|库存|现货|发货|配送|快递|"
        r"下单|购买|想买|要买|尺寸|颜色"
    ),
)
_HUMAN_REASONS = (
    (
        "human_handoff",
        re.compile(
            r"\b(?:need|want|request)\s+(?:an?\s+|the\s+)?"
            r"(?:human(?:\s+agent)?|agent|operator|representative|manager|real\s+person)\b|"
            r"\b(?:connect|speak|talk|transfer)\b.{0,40}"
            r"\b(?:human(?:\s+agent)?|agent|operator|representative|manager|real\s+person)\b|"
            r"\b(?:human(?:\s+agent)?|agent|operator|representative|manager|real\s+person)\s+please\b|"
            r"转(?:接)?(?:到|给)?人工(?:客服|服务)?|"
            r"(?:找|联系|接通|请让|让|转给).{0,10}"
            r"(?:人工(?:客服|服务)?|客服|经理)|"
            r"(?:需要|要|请给我).{0,6}(?:人工(?:客服|服务)|真人客服)",
            re.IGNORECASE,
        ),
    ),
    (
        "payment",
        re.compile(
            r"\b(payment|paid|pay failed|payment failed)\b|支付|付款",
            re.IGNORECASE,
        ),
    ),
    (
        "refund",
        re.compile(r"\b(refund|chargeback)\b|退款|退钱|拒付", re.IGNORECASE),
    ),
    ("complaint", re.compile(r"\bcomplain(?:t)?\b|投诉", re.IGNORECASE)),
    (
        "cancellation",
        re.compile(r"\bcancel(?:lation)?\b|取消(?:订单)?", re.IGNORECASE),
    ),
    (
        "unsupported_decision",
        re.compile(
            r"\b(special|custom|lowest|best)\s+(price|discount)\b|"
            r"\b(guarantee|promise)\b|特殊价|最低价|额外折扣|保证|承诺",
            re.IGNORECASE,
        ),
    ),
)
_PRIVATE_CHANNEL_ACCEPTANCE_PATTERNS = (
    re.compile(
        r"\b(yes|sure|okay|ok|fine|great|happy to)\b.{0,30}"
        r"\b(whats?app|wechat|telegram|line)\b|"
        r"\b(continue|move|chat|talk|message)\b.{0,20}"
        r"\b(on|via|to)\s+(whats?app|wechat|telegram|line)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(好的|可以|行|没问题|加我|加你).{0,12}"
        r"(微信|WhatsApp|Telegram|Line)|加微信聊",
        re.IGNORECASE,
    ),
)


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(text)).split())


def extract_contact(text: str) -> str:
    normalized = _normalize(text)
    email = _EMAIL_PATTERN.search(normalized)
    if email:
        return email.group(1)
    for pattern in _HANDLE_PATTERNS:
        handle = pattern.search(normalized)
        if handle:
            return handle.group(1)
    for match in _LABELED_PHONE_PATTERN.finditer(normalized):
        candidate = match.group(1).strip(" -()")
        digit_count = sum(character.isdigit() for character in candidate)
        if 7 <= digit_count <= 15:
            return candidate
    for match in _PLAUSIBLE_PHONE_PATTERN.finditer(normalized):
        prefix = normalized[max(0, match.start(1) - 40) : match.start(1)]
        if _IDENTIFIER_CONTEXT_PATTERN.search(prefix):
            continue
        candidate = match.group(1).strip(" -()")
        digit_count = sum(character.isdigit() for character in candidate)
        if 7 <= digit_count <= 15:
            return candidate
    return ""


def is_meaningful(text: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    semantic = "".join(
        character.lower()
        for character in normalized
        if character.isalnum() or "\u4e00" <= character <= "\u9fff"
    )
    if not semantic or semantic in _ACKNOWLEDGEMENTS:
        return False
    return True


def _human_reason(text: str) -> str:
    normalized = _normalize(text)
    for reason, pattern in _HUMAN_REASONS:
        if pattern.search(normalized):
            return reason
    return ""


def requires_human(text: str) -> bool:
    return bool(_human_reason(text))


def shows_buying_intent(text: str) -> bool:
    normalized = _normalize(text)
    return any(pattern.search(normalized) for pattern in _BUYING_PATTERNS)


def _accepts_private_channel(text: str) -> bool:
    normalized = _normalize(text)
    return any(
        pattern.search(normalized) for pattern in _PRIVATE_CHANNEL_ACCEPTANCE_PATTERNS
    )


def assess_inbound(
    previous_stage: ConversationStage,
    text: str,
    meaningful_turns: int,
    invite_after_meaningful_turns: int,
    last_invited_at_ms: int,
    now_ms: int,
) -> ConversionAssessment:
    previous_stage = ConversationStage(previous_stage)
    meaningful = is_meaningful(text)
    if previous_stage in {ConversationStage.HUMAN_REQUIRED, ConversationStage.CLOSED}:
        return ConversionAssessment(previous_stage, meaningful, False)

    human_reason = _human_reason(text)
    if human_reason:
        return ConversionAssessment(
            ConversationStage.HUMAN_REQUIRED,
            meaningful,
            False,
            human_reason=human_reason,
        )

    contact = extract_contact(text)
    accepted = _accepts_private_channel(text)
    if contact or accepted:
        return ConversionAssessment(
            ConversationStage.CONTACT_CAPTURED,
            meaningful,
            False,
            contact=contact,
            human_reason="private_channel_accepted" if accepted and not contact else "",
        )

    if previous_stage == ConversationStage.CONTACT_CAPTURED:
        return ConversionAssessment(previous_stage, meaningful, False)

    buying_intent = shows_buying_intent(text)
    turn_count = max(0, meaningful_turns) + int(meaningful)
    threshold = max(1, invite_after_meaningful_turns)
    cooldown_active = (
        last_invited_at_ms > 0 and now_ms - last_invited_at_ms < INVITE_COOLDOWN_MS
    )
    should_invite = (buying_intent or turn_count >= threshold) and not cooldown_active

    if previous_stage == ConversationStage.INVITED:
        stage = ConversationStage.INVITED
    elif buying_intent or turn_count >= threshold:
        stage = ConversationStage.QUALIFIED
    elif previous_stage == ConversationStage.QUALIFIED:
        stage = ConversationStage.QUALIFIED
    elif previous_stage == ConversationStage.NEW and meaningful:
        stage = ConversationStage.ENGAGED
    else:
        stage = previous_stage
    return ConversionAssessment(stage, meaningful, should_invite)


def _bounded_fragment(value: str, limit: int = PROMPT_FRAGMENT_LIMIT) -> str:
    return _normalize(value)[:limit]


def build_lead_prompt(
    *,
    offer_context: str,
    faq_context: str,
    stage: ConversationStage,
    should_invite: bool,
    private_channel_destination: str,
) -> str:
    offer = _bounded_fragment(offer_context)
    faq = _bounded_fragment(faq_context)
    stage_value = _bounded_fragment(ConversationStage(stage).value, 32)
    parts = [
        "Reply in the sender's language. Be concise and ask at most one question.",
        f"Stage: {stage_value}",
        f"Account offer: {offer}",
        f"FAQ: {faq}",
        "Use only the supplied account facts. Escalate decisions not covered by them.",
    ]
    if should_invite:
        destination = _bounded_fragment(
            private_channel_destination, PROMPT_DESTINATION_LIMIT
        )
        if destination:
            parts.append(
                "Include one private-channel invitation using exactly this destination: "
                f"{destination}"
            )
    return "\n".join(parts)[:PROMPT_LIMIT]

import inspect

import pytest

from tikpoc.lead_conversion import (
    ConversationStage,
    assess_inbound,
    build_lead_prompt,
    extract_contact,
    is_meaningful,
    requires_human,
    shows_buying_intent,
)


DAY_MS = 24 * 60 * 60 * 1000


def _assess(
    text: str,
    *,
    previous_stage: ConversationStage = ConversationStage.ENGAGED,
    meaningful_turns: int = 0,
    threshold: int = 2,
    last_invited_at_ms: int = 0,
    now_ms: int = 100_000,
):
    return assess_inbound(
        previous_stage=previous_stage,
        text=text,
        meaningful_turns=meaningful_turns,
        invite_after_meaningful_turns=threshold,
        last_invited_at_ms=last_invited_at_ms,
        now_ms=now_ms,
    )


def test_second_meaningful_turn_invites_to_private_channel() -> None:
    result = _assess(
        "Can you show me the available black bags?",
        meaningful_turns=1,
    )

    assert result.stage == ConversationStage.QUALIFIED
    assert result.meaningful is True
    assert result.should_invite is True


def test_contact_capture_has_priority_over_invitation() -> None:
    result = _assess(
        "My WhatsApp is +44 7700 900123",
        previous_stage=ConversationStage.INVITED,
        meaningful_turns=3,
        last_invited_at_ms=1,
    )

    assert result.stage == ConversationStage.CONTACT_CAPTURED
    assert result.contact == "+44 7700 900123"
    assert result.should_invite is False


@pytest.mark.parametrize(
    "text",
    [
        "I want a refund",
        "payment failed",
        "我要投诉",
        "Please cancel my order",
        "I need a chargeback",
        "你能保证明天到吗",
        "Can you decide a special price for me?",
    ],
)
def test_payment_complaint_or_unsupported_decision_requires_human(text: str) -> None:
    assert requires_human(text) is True
    result = _assess(text, meaningful_turns=1)
    assert result.stage == ConversationStage.HUMAN_REQUIRED
    assert result.human_reason
    assert result.should_invite is False


@pytest.mark.parametrize(
    "text",
    [
        "I need a human agent",
        "Please connect me to an operator",
        "Can I speak with a representative?",
        "I want to talk to a manager",
        "转人工客服",
        "我要找客服",
        "请让经理联系我",
    ],
)
def test_explicit_handoff_request_requires_human(text: str) -> None:
    assert requires_human(text) is True
    result = _assess(text, meaningful_turns=1)
    assert result.stage == ConversationStage.HUMAN_REQUIRED
    assert result.human_reason == "human_handoff"
    assert result.should_invite is False


@pytest.mark.parametrize(
    "text",
    [
        "Do you have this product in stock?",
        "What is the price?",
        "Can you ship to London?",
        "I want to buy it",
        "这个产品有货吗？",
        "多少钱？",
        "可以发货到上海吗？",
        "我想下单",
    ],
)
def test_buying_signal_invites_immediately_in_english_and_chinese(text: str) -> None:
    assert shows_buying_intent(text) is True
    result = _assess(text, meaningful_turns=0, threshold=10)
    assert result.stage == ConversationStage.QUALIFIED
    assert result.should_invite is True


def test_invitation_cooldown_blocks_before_24_hours_and_allows_boundary() -> None:
    last_invited = 1_000

    blocked = _assess(
        "I want to buy it",
        last_invited_at_ms=last_invited,
        now_ms=last_invited + DAY_MS - 1,
    )
    permitted = _assess(
        "I want to buy it",
        last_invited_at_ms=last_invited,
        now_ms=last_invited + DAY_MS,
    )

    assert blocked.should_invite is False
    assert permitted.should_invite is True


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Email: buyer@example.com", "buyer@example.com"),
        ("微信号：buyer_2026", "buyer_2026"),
        ("WeChat ID: wx.buyer-7", "wx.buyer-7"),
        ("Telegram: @buyer_shop", "@buyer_shop"),
        ("Line ID: buyer.line", "buyer.line"),
        ("电话：１３８ ００１３ ８０００", "138 0013 8000"),
    ],
)
def test_extracts_english_and_chinese_contacts(text: str, expected: str) -> None:
    assert extract_contact(text) == expected


@pytest.mark.parametrize("text", ["order 1234", "ID: 99881", "code 123456"])
def test_does_not_extract_obvious_short_numeric_ids(text: str) -> None:
    assert extract_contact(text) == ""


@pytest.mark.parametrize(
    "text",
    [
        "Yes, let's continue on WhatsApp",
        "好的，加微信聊",
    ],
)
def test_private_channel_acceptance_captures_without_contact(text: str) -> None:
    result = _assess(
        text,
        previous_stage=ConversationStage.INVITED,
        meaningful_turns=4,
    )

    assert result.stage == ConversationStage.CONTACT_CAPTURED
    assert result.contact == ""
    assert result.human_reason == "private_channel_accepted"
    assert result.should_invite is False


@pytest.mark.parametrize(
    "text",
    ["", "  hello  ", "Hi!", "thanks", "谢谢", "👍👍", "ok 😊"],
)
def test_acknowledgements_are_not_meaningful(text: str) -> None:
    assert is_meaningful(text) is False


@pytest.mark.parametrize("text", ["Which size fits?", "我需要一个黑色的包"])
def test_questions_and_product_needs_are_meaningful(text: str) -> None:
    assert is_meaningful(text) is True


def test_new_conversation_becomes_engaged_without_qualifying_signal() -> None:
    result = _assess(
        "I like the blue style",
        previous_stage=ConversationStage.NEW,
        threshold=5,
    )
    assert result.stage == ConversationStage.ENGAGED


@pytest.mark.parametrize(
    "stage",
    [ConversationStage.INVITED, ConversationStage.CONTACT_CAPTURED],
)
def test_later_nonterminal_stages_do_not_regress(stage: ConversationStage) -> None:
    result = _assess("hello", previous_stage=stage)
    assert result.stage == stage
    assert result.should_invite is False


@pytest.mark.parametrize(
    "stage", [ConversationStage.HUMAN_REQUIRED, ConversationStage.CLOSED]
)
def test_terminal_stages_ignore_later_inbound(stage: ConversationStage) -> None:
    result = _assess(
        "My WhatsApp is +44 7700 900123 and I want to buy",
        previous_stage=stage,
    )
    assert result.stage == stage
    assert result.contact == ""
    assert result.should_invite is False


def test_prompt_api_is_keyword_only() -> None:
    signature = inspect.signature(build_lead_prompt)
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )


def test_prompt_includes_bounded_context_and_destination_only_for_invite() -> None:
    common = {
        "offer_context": "  Catalog   bags " + "O" * 10_000,
        "faq_context": "Shipping FAQ " + "F" * 20_000,
        "stage": ConversationStage.QUALIFIED,
    }

    invite_prompt = build_lead_prompt(
        **common,
        should_invite=True,
        private_channel_destination="WhatsApp: +1 555 0100" + "D" * 10_000,
    )
    regular_prompt = build_lead_prompt(
        **common,
        should_invite=False,
        private_channel_destination="SECRET_DESTINATION",
    )

    assert "Catalog bags" in invite_prompt
    assert "Shipping FAQ" in invite_prompt
    assert "Stage: qualified" in invite_prompt
    assert "WhatsApp: +1 555 0100" in invite_prompt
    assert len(invite_prompt) <= 8_000
    assert "SECRET_DESTINATION" not in regular_prompt
    assert "private-channel invitation" not in regular_prompt

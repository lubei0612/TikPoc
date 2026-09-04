import inspect
import sqlite3
from datetime import UTC, datetime

import pytest

from tikpoc.db import Database
from tikpoc.lead_conversion import (
    ConversationStage,
    assess_inbound,
    build_lead_prompt,
    extract_contact,
    is_meaningful,
    preferred_private_channel,
    requires_human,
    shows_buying_intent,
)

DAY_MS = 24 * 60 * 60 * 1000


def test_lead_funnel_timeline_is_account_scoped_zero_filled_and_utc(
    tmp_path,
) -> None:
    database = Database(tmp_path / "timeline.db")
    database.migrate()
    start_ms = int(datetime(2026, 8, 22, tzinfo=UTC).timestamp() * 1_000)
    for account_id, stage, day, suffix in (
        ("account-01", "dm_inbound", 0, "inbound-1"),
        ("account-01", "qualified", 0, "qualified-1"),
        ("account-02", "invited", 13, "invited-1"),
        ("excluded", "contact_captured", 7, "excluded-contact"),
    ):
        database.record_lead_funnel_event(
            account_id,
            f"buyer-{suffix}",
            stage,
            suffix,
            occurred_at_ms=start_ms + day * DAY_MS + 1_000,
        )
    database.record_lead_sale(
        "account-02",
        "buyer-sale",
        amount_minor=12_500,
        currency="USD",
        status="confirmed",
        occurred_at_ms=start_ms + 13 * DAY_MS + 2_000,
    )
    database.record_lead_sale(
        "excluded",
        "buyer-excluded-sale",
        amount_minor=99_999,
        currency="USD",
        status="confirmed",
        occurred_at_ms=start_ms + 3 * DAY_MS,
    )

    timeline = database.lead_funnel_timeline(
        ("account-01", "account-02"), start_ms=start_ms, days=14
    )

    assert len(timeline) == 14
    assert [row["date"] for row in timeline] == [
        datetime.fromtimestamp((start_ms + day * DAY_MS) / 1_000, tz=UTC)
        .date()
        .isoformat()
        for day in range(14)
    ]
    assert timeline[0] == {
        "date": "2026-08-22",
        "dm_inbound": 1,
        "qualified": 1,
        "invited": 0,
        "contact_captured": 0,
        "sales": 0,
    }
    assert timeline[7] == {
        "date": "2026-08-29",
        "dm_inbound": 0,
        "qualified": 0,
        "invited": 0,
        "contact_captured": 0,
        "sales": 0,
    }
    assert timeline[-1]["invited"] == 1
    assert timeline[-1]["sales"] == 1


def test_lead_automation_snapshot_is_account_scoped_and_derived_from_rows(
    tmp_path,
) -> None:
    database = Database(tmp_path / "automation.db")
    database.migrate()
    with sqlite3.connect(database.path) as connection:
        inbound_rows = []
        plan_rows = []
        for index in range(486):
            account_id = "account-01" if index % 2 == 0 else "account-02"
            message_id = f"inbound-{index:03d}"
            inbound_rows.append(
                (account_id, f"conversation-{index:03d}", message_id, index + 1)
            )
            if index < 348:
                state = (
                    "sent"
                    if index < 331
                    else "uncertain"
                    if index < 336
                    else "superseded"
                )
                plan_rows.append(
                    (
                        account_id,
                        f"conversation-{index:03d}",
                        message_id,
                        f"buyer-{index:03d}",
                        index + 1,
                        state,
                        "ai",
                        message_id,
                    )
                )
            elif index < 446:
                plan_rows.append(
                    (
                        account_id,
                        f"conversation-{index:03d}",
                        f"manual-{index:03d}",
                        f"buyer-{index:03d}",
                        index + 1,
                        "sent",
                        "manual",
                        message_id,
                    )
                )
        connection.executemany(
            """
            INSERT INTO web_messages(
                account_id, conversation_id, message_id, direction,
                message_type, timestamp_ms
            ) VALUES (?, ?, ?, 'inbound', 'TEXT', ?)
            """,
            inbound_rows,
        )
        connection.executemany(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                participant_username, inbound_timestamp_ms, reply_text,
                state, plan_origin, source_inbound_fingerprint
            ) VALUES (?, ?, ?, ?, ?, 'reply', ?, ?, ?)
            """,
            plan_rows,
        )
        connection.executemany(
            """
            INSERT INTO lead_funnel_events(
                account_id, participant_username, stage, source_key, occurred_at_ms
            ) VALUES (?, ?, 'human_required', ?, ?)
            """,
            (
                (
                    "account-01" if index % 2 == 0 else "account-02",
                    f"buyer-{index:03d}",
                    f"human-{index:03d}",
                    index + 1,
                )
                for index in range(446, 474)
            ),
        )
        connection.execute(
            """
            INSERT INTO web_messages(
                account_id, conversation_id, message_id, direction,
                message_type, timestamp_ms
            ) VALUES ('excluded', 'excluded-conversation', 'excluded-inbound',
                      'inbound', 'TEXT', 1)
            """
        )

    assert database.lead_automation_snapshot(("account-01", "account-02")) == {
        "ai_plans": 348,
        "ai_sent": 331,
        "ai_uncertain": 5,
        "ai_superseded": 12,
        "manual_handled": 98,
        "human_required": 28,
        "pending_inbound": 29,
        "automatic_handling_rate": 0.724,
    }
    assert database.lead_automation_snapshot(()) == {
        "ai_plans": 0,
        "ai_sent": 0,
        "ai_uncertain": 0,
        "ai_superseded": 0,
        "manual_handled": 0,
        "human_required": 0,
        "pending_inbound": 0,
        "automatic_handling_rate": 0.0,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("WhatsApp please", "whatsapp"),
        ("Telegram works for me", "telegram"),
        ("可以用 TG 联系", "telegram"),
        ("Tell me more here", ""),
    ],
)
def test_preferred_private_channel(text: str, expected: str) -> None:
    assert preferred_private_channel(text) == expected


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
def test_payment_complaint_or_unsupported_decision_routes_to_profile_contact(
    text: str,
) -> None:
    assert requires_human(text) is True
    result = _assess(text, meaningful_turns=1)
    assert result.stage == ConversationStage.QUALIFIED
    assert result.profile_contact_reason
    assert result.should_invite is True


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
def test_explicit_handoff_request_routes_to_profile_contact(text: str) -> None:
    assert requires_human(text) is True
    result = _assess(text, meaningful_turns=1)
    assert result.stage == ConversationStage.QUALIFIED
    assert result.profile_contact_reason == "human_handoff"
    assert result.should_invite is True


@pytest.mark.parametrize(
    "text",
    [
        "stop messaging me",
        "不要再联系我",
        "hör auf mir zu folgen",
        "no me contactes más",
        "ne me contactez plus",
    ],
)
def test_explicit_stop_contact_closes_without_reply(text: str) -> None:
    result = _assess(text, meaningful_turns=1)

    assert result.stage == ConversationStage.CLOSED
    assert result.stop_contact_reason == "explicit_opt_out"
    assert result.should_invite is False


def test_product_preference_rejection_remains_replyable() -> None:
    result = _assess("I am not interested in red; do you have black?")

    assert result.stage != ConversationStage.CLOSED
    assert result.stop_contact_reason == ""


@pytest.mark.parametrize(
    "text",
    [
        "The agent was helpful yesterday",
        "This manager bag looks good",
        "I want this manager bag",
        "你们客服回复很快",
        "经理款的包还有吗",
        "我想要经理款的包",
    ],
)
def test_human_role_mentions_without_a_request_do_not_trigger_handoff(
    text: str,
) -> None:
    assert requires_human(text) is False


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


@pytest.mark.parametrize(
    "text",
    [
        "order 1234",
        "ID: 99881",
        "code 123456",
        "order number 1234567890",
        "tracking number 123456789012",
        "商品编号 12345678",
    ],
)
def test_does_not_extract_numeric_identifiers_as_contacts(text: str) -> None:
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


def test_closed_stage_ignores_later_inbound() -> None:
    result = _assess(
        "My WhatsApp is +44 7700 900123 and I want to buy",
        previous_stage=ConversationStage.CLOSED,
    )
    assert result.stage == ConversationStage.CLOSED
    assert result.contact == ""
    assert result.should_invite is False


def test_legacy_human_required_stage_reopens_for_autonomous_service() -> None:
    result = _assess(
        "My WhatsApp is +44 7700 900123 and I want to buy",
        previous_stage=ConversationStage.HUMAN_REQUIRED,
    )

    assert result.stage == ConversationStage.CONTACT_CAPTURED
    assert result.contact == "+44 7700 900123"


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

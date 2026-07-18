import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier

import pytest

from tikpoc.browser_dm import BrowserDmService, BrowserInbound, BrowserReply
from tikpoc.db import Database
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


class FakeReplyClient:
    def __init__(self, replies: tuple[str, ...] = ("Draft reply",)) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[list[dict[str, object]], dict[str, object]]] = []

    def reply_conversation(
        self, history: list[dict[str, object]], **kwargs: object
    ) -> str:
        self.calls.append((history, kwargs))
        return self.replies.pop(0)


class FailingOnceReplyClient(FakeReplyClient):
    def __init__(self) -> None:
        super().__init__(("Recovered draft",))
        self.attempts = 0

    def reply_conversation(
        self, history: list[dict[str, object]], **kwargs: object
    ) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("model call interrupted")
        return super().reply_conversation(history, **kwargs)


def registry_with_browser_account(**overrides: object) -> WebAccountRegistry:
    values: dict[str, object] = {
        "account_id": "account-01",
        "device_id": "phone-01",
        "mode": "browser",
        "private_channel_hint": "WhatsApp: +1 555 0100",
        "offer_context": "Bags from the current catalog",
        "faq_text": "Shipping takes 5-7 days.",
        "max_auto_replies": 12,
        "invite_after_meaningful_turns": 2,
        "fallback_acknowledgement": "Thanks for your message.",
        "browser_dm_enabled": True,
        "enabled": True,
    }
    values.update(overrides)
    return WebAccountRegistry(
        (
            WebAccount(**values),  # type: ignore[arg-type]
        )
    )


def test_same_inbound_fingerprint_generates_one_ai_draft(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FakeReplyClient()
    service = BrowserDmService(
        database, registry_with_browser_account(), ai, clock=lambda: 100.0
    )
    inbound = BrowserInbound(
        "account-01",
        "phone-01",
        "conversation-01",
        "fp-01",
        "buyer",
        "Do you ship?",
        99_000,
    )

    first = service.plan(inbound)
    second = service.plan(inbound)

    assert first.plan_id == second.plan_id
    assert first.reply_text == second.reply_text
    assert len(ai.calls) == 1


def test_new_plan_passes_account_context_and_advances_inbound_state_once(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    destination = "WhatsApp: +1 555 0100"
    ai = FakeReplyClient((f"Yes. Continue on {destination}",))
    service = BrowserDmService(
        database, registry_with_browser_account(), ai, clock=lambda: 100.0
    )
    inbound = BrowserInbound(
        "account-01",
        "phone-01",
        "conversation-01",
        "fp-context",
        "buyer",
        "Do you ship?",
        99_000,
    )

    first = service.plan(inbound)
    second = service.plan(inbound)
    state = database.browser_conversation_state("account-01", "conversation-01")

    assert first == second
    assert first.stage == "invited"
    assert len(ai.calls) == 1
    history, options = ai.calls[0]
    assert [item["message_id"] for item in history] == ["fp-context"]
    assert options == {
        "private_channel_hint": destination,
        "offer_context": "Bags from the current catalog",
        "faq_context": "Shipping takes 5-7 days.",
        "conversation_stage": "qualified",
        "should_invite": True,
        "fallback": "Thanks for your message.",
        "max_history_messages": 12,
    }
    assert state.stage == "qualified"
    assert state.meaningful_turns == 1
    assert state.auto_reply_count == 0
    assert state.last_invited_at_ms == 0


def test_reply_budget_closes_without_calling_ai(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    for index in range(12):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"inbound-{index}",
            direction="inbound",
            message_type="TEXT",
            text=f"question {index}",
            timestamp_ms=index * 2,
            participant_username="buyer",
        )
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"outbound-{index}",
            direction="outbound",
            message_type="TEXT",
            text=f"answer {index}",
            timestamp_ms=index * 2 + 1,
            in_reply_to_message_id=f"inbound-{index}",
        )
    ai = FakeReplyClient()
    service = BrowserDmService(
        database, registry_with_browser_account(max_auto_replies=12), ai
    )

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-budget",
            "buyer",
            "one more question",
            99_000,
        )
    )

    assert reply.stage == "closed"
    assert reply.reply_text == ""
    assert ai.calls == []
    state = database.browser_conversation_state("account-01", "conversation-01")
    assert state.stage == "closed"


def test_human_required_plan_is_empty_and_does_not_call_ai(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FakeReplyClient()
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-human",
            "buyer",
            "I need a refund",
            99_000,
        )
    )

    assert reply.stage == "human_required"
    assert reply.reply_text == ""
    assert ai.calls == []
    state = database.browser_conversation_state("account-01", "conversation-01")
    assert state.stage == "human_required"
    assert state.human_required is True


def test_missing_invite_configuration_is_recorded_once_without_queueing(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FakeReplyClient(("I can answer that here.",))
    service = BrowserDmService(
        database,
        registry_with_browser_account(private_channel_hint=""),
        ai,
        clock=lambda: 100.0,
    )
    inbound = BrowserInbound(
        "account-01",
        "phone-01",
        "conversation-01",
        "fp-missing-invite",
        "buyer",
        "Do you ship?",
        99_000,
    )

    first = service.plan(inbound)
    second = service.plan(inbound)

    assert first == second
    assert first.stage == "qualified"
    assert ai.calls[0][1]["should_invite"] is False
    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT event_type, dedup_key, payload_json, state
            FROM web_events
            WHERE account_id='account-01'
              AND event_type='invite_configuration_missing'
            """
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["dedup_key"] == "fp-missing-invite"
    assert json.loads(rows[0]["payload_json"]) == {"conversation_id": "conversation-01"}
    assert rows[0]["state"] == "completed"


def test_confirmed_result_appends_outbound_and_counts_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        FakeReplyClient(("Draft reply",)),
        clock=lambda: 100.0,
    )
    plan = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-sent",
            "buyer",
            "Tell me more about the style",
            99_000,
        )
    )

    assert service.record_result("account-01", "phone-01", plan.plan_id, "sent")
    assert service.record_result("account-01", "phone-01", plan.plan_id, "sent")

    messages = database.recent_web_messages("account-01", "conversation-01", limit=20)
    assert [message["direction"] for message in messages] == ["inbound", "outbound"]
    assert messages[1]["text"] == "Draft reply"
    state = database.browser_conversation_state("account-01", "conversation-01")
    stored_plan = database.browser_reply_plan_by_id(plan.plan_id)
    assert state.auto_reply_count == 1
    assert state.stage == "engaged"
    assert stored_plan is not None
    assert stored_plan.state == "sent"


def test_uncertain_invitation_advances_only_after_confirmed_send(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    destination = "WhatsApp: +1 555 0100"
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        FakeReplyClient((f"Continue on {destination}",)),
        clock=lambda: 100.0,
    )
    plan = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-invite",
            "buyer",
            "Do you ship?",
            99_000,
        )
    )
    assert plan.stage == "invited"

    assert service.record_result("account-01", "phone-01", plan.plan_id, "uncertain")
    uncertain_state = database.browser_conversation_state(
        "account-01", "conversation-01"
    )
    assert uncertain_state.stage == "qualified"
    assert uncertain_state.auto_reply_count == 0
    assert uncertain_state.last_invited_at_ms == 0
    assert [
        item["direction"]
        for item in database.recent_web_messages(
            "account-01", "conversation-01", limit=20
        )
    ] == ["inbound"]

    assert service.record_result("account-01", "phone-01", plan.plan_id, "sent")
    assert service.record_result("account-01", "phone-01", plan.plan_id, "sent")
    sent_state = database.browser_conversation_state("account-01", "conversation-01")
    assert sent_state.stage == "invited"
    assert sent_state.auto_reply_count == 1
    assert sent_state.last_invited_at_ms == 100_000


def test_superseded_result_never_appends_outbound(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database, registry_with_browser_account(), FakeReplyClient()
    )
    plan = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-superseded",
            "buyer",
            "Tell me about this style",
            99_000,
        )
    )

    assert service.record_result("account-01", "phone-01", plan.plan_id, "superseded")
    assert not service.record_result("account-01", "phone-01", plan.plan_id, "sent")
    messages = database.recent_web_messages("account-01", "conversation-01", limit=20)
    assert [message["direction"] for message in messages] == ["inbound"]
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).auto_reply_count
        == 0
    )


@pytest.mark.parametrize("state", ["planned", "failed", ""])
def test_record_result_rejects_unknown_state(tmp_path: Path, state: str) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database, registry_with_browser_account(), FakeReplyClient()
    )

    with pytest.raises(ValueError, match="result state"):
        service.record_result("account-01", "phone-01", 1, state)


def test_record_result_validates_device_and_plan(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database, registry_with_browser_account(), FakeReplyClient()
    )
    plan = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-owner",
            "buyer",
            "Tell me about this style",
            99_000,
        )
    )

    with pytest.raises(ValueError, match="mapping"):
        service.record_result("account-01", "phone-02", plan.plan_id, "sent")
    with pytest.raises(KeyError):
        service.record_result("account-01", "phone-01", 99_999, "sent")


def test_existing_planning_reservation_recovers_inbound_atomically(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    reserved, created = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "fp-recovery",
        "buyer",
        "Tell me about this style",
        99_000,
    )
    assert created is True
    ai = FakeReplyClient(("Recovered draft",))
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-recovery",
            "buyer",
            "Tell me about this style",
            99_000,
        )
    )

    assert reply.plan_id == reserved.id
    assert reply.reply_text == "Recovered draft"
    assert len(ai.calls) == 1
    messages = database.recent_web_messages("account-01", "conversation-01", limit=20)
    assert [item["message_id"] for item in messages] == ["fp-recovery"]


def test_human_request_has_priority_when_reply_budget_is_reached(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    for index in range(12):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"outbound-{index}",
            direction="outbound",
            message_type="TEXT",
            text="previous reply",
            timestamp_ms=index,
            participant_username="buyer",
        )
    ai = FakeReplyClient()
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-budget-human",
            "buyer",
            "I need a refund",
            99_000,
        )
    )

    assert reply.stage == "human_required"
    assert reply.reply_text == ""
    assert ai.calls == []


def test_ai_exception_leaves_planning_row_for_exact_retry(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FailingOnceReplyClient()
    service = BrowserDmService(database, registry_with_browser_account(), ai)
    inbound = BrowserInbound(
        "account-01",
        "phone-01",
        "conversation-01",
        "fp-ai-retry",
        "buyer",
        "Tell me about this style",
        99_000,
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        service.plan(inbound)
    retained = database.get_browser_reply_plan("account-01", "fp-ai-retry")
    state_before_retry = database.browser_conversation_state(
        "account-01", "conversation-01"
    )
    assert retained is not None
    assert retained.state == "planning"
    assert retained.reply_text == ""
    assert state_before_retry.stage == "new"
    assert state_before_retry.meaningful_turns == 0

    reply = service.plan(inbound)

    assert reply.plan_id == retained.id
    assert reply.reply_text == "Recovered draft"
    assert ai.attempts == 2
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).meaningful_turns
        == 1
    )


def test_concurrent_same_inbound_uses_one_model_draft(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FakeReplyClient(("One draft",))
    service = BrowserDmService(database, registry_with_browser_account(), ai)
    inbound = BrowserInbound(
        "account-01",
        "phone-01",
        "conversation-01",
        "fp-concurrent",
        "buyer",
        "Tell me about this style",
        99_000,
    )
    barrier = Barrier(2)

    def plan_once():
        barrier.wait()
        return service.plan(inbound)

    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = [
            future.result()
            for future in [
                executor.submit(plan_once),
                executor.submit(plan_once),
            ]
        ]

    assert replies[0] == replies[1]
    assert len(ai.calls) == 1


@pytest.mark.parametrize(
    "inbound",
    [
        BrowserInbound("unknown", "phone-01", "c", "fp", "buyer", "hello", 1),
        BrowserInbound("account-01", "wrong", "c", "fp", "buyer", "hello", 1),
        BrowserInbound("account-01", "phone-01", "", "fp", "buyer", "hello", 1),
        BrowserInbound("account-01", "phone-01", "c", "fp", "buyer", "", 1),
        BrowserInbound("account-01", "phone-01", "c", "fp", "buyer", "hello", -1),
    ],
)
def test_plan_rejects_invalid_identity_or_timestamp(
    tmp_path: Path, inbound: BrowserInbound
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database, registry_with_browser_account(), FakeReplyClient()
    )

    with pytest.raises(ValueError):
        service.plan(inbound)


@pytest.mark.parametrize(
    "account_overrides",
    [
        {"enabled": False},
        {"browser_dm_enabled": False},
        {
            "mode": "business",
            "business_id": "business-01",
            "token_file": Path("token.json"),
        },
    ],
)
def test_plan_rejects_disabled_or_nonbrowser_account(
    tmp_path: Path, account_overrides: dict[str, object]
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database,
        registry_with_browser_account(**account_overrides),
        FakeReplyClient(),
    )

    with pytest.raises(ValueError, match="disabled"):
        service.plan(
            BrowserInbound("account-01", "phone-01", "c", "fp", "buyer", "hello", 1)
        )


def test_cooldown_timestamp_changes_only_when_draft_contains_invitation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    destination = "WhatsApp: +1 555 0100"
    ai = FakeReplyClient(
        (
            f"Continue on {destination}",
            "The current catalog has several options.",
        )
    )
    now = [100.0]
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        ai,
        clock=lambda: now[0],
    )
    first = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-first-invite",
            "buyer",
            "Do you ship?",
            99_000,
        )
    )
    assert service.record_result("account-01", "phone-01", first.plan_id, "sent")
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).last_invited_at_ms
        == 100_000
    )

    now[0] = 101.0
    second = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-cooldown",
            "buyer",
            "Which colors are available?",
            100_500,
        )
    )
    assert ai.calls[1][1]["should_invite"] is False
    assert destination not in second.reply_text
    assert second.stage == "invited"
    assert service.record_result("account-01", "phone-01", second.plan_id, "sent")

    state = database.browser_conversation_state("account-01", "conversation-01")
    assert state.last_invited_at_ms == 100_000
    assert state.auto_reply_count == 2


def test_provider_fallback_without_destination_is_not_planned_as_invited(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        FakeReplyClient(("Thanks for your message.",)),
    )

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-fallback",
            "buyer",
            "Do you ship?",
            99_000,
        )
    )

    assert reply.stage == "qualified"
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).last_invited_at_ms
        == 0
    )


def test_contact_capture_updates_state_before_acknowledgement_send(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    ai = FakeReplyClient(("Thank you. A person will follow up.",))
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        ai,
        clock=lambda: 100.0,
    )

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-contact",
            "buyer",
            "My WhatsApp is +44 7700 900123",
            99_000,
        )
    )

    state = database.browser_conversation_state("account-01", "conversation-01")
    assert reply.stage == "contact_captured"
    assert ai.calls[0][1]["should_invite"] is False
    assert state.stage == "contact_captured"
    assert state.contact_captured_at_ms == 100_000
    assert state.auto_reply_count == 0


def test_result_reconciliation_remains_available_after_account_is_disabled(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    planning_service = BrowserDmService(
        database, registry_with_browser_account(), FakeReplyClient()
    )
    plan = planning_service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-disabled-result",
            "buyer",
            "Tell me about this style",
            99_000,
        )
    )
    reconciliation_service = BrowserDmService(
        database,
        registry_with_browser_account(enabled=False, browser_dm_enabled=False),
        FakeReplyClient(),
    )

    assert reconciliation_service.record_result(
        "account-01", "phone-01", plan.plan_id, "sent"
    )
    stored = database.browser_reply_plan_by_id(plan.plan_id)
    assert stored is not None
    assert stored.state == "sent"


def test_reply_budget_uses_durable_counter_when_message_history_is_missing(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "seed",
        direction="inbound",
        message_type="TEXT",
        text="seed",
        timestamp_ms=1,
        participant_username="buyer",
    )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations SET auto_reply_count=12
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
        connection.execute(
            """
            DELETE FROM web_messages
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
    ai = FakeReplyClient()
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-counter-budget",
            "buyer",
            "one more question",
            99_000,
        )
    )

    assert reply.stage == "closed"
    assert reply.reply_text == ""
    assert ai.calls == []


def test_same_inbound_key_is_scoped_by_account(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    registry = WebAccountRegistry(
        (
            registry_with_browser_account().accounts[0],
            WebAccount(
                account_id="account-02",
                device_id="phone-02",
                mode="browser",
                offer_context="Shoes from the current catalog",
            ),
        )
    )
    ai = FakeReplyClient(("First draft", "Second draft"))
    service = BrowserDmService(database, registry, ai)

    first = service.plan(
        BrowserInbound(
            "account-01", "phone-01", "c-1", "same-key", "buyer", "hello one", 1
        )
    )
    second = service.plan(
        BrowserInbound(
            "account-02", "phone-02", "c-2", "same-key", "buyer", "hello two", 2
        )
    )

    assert first.plan_id != second.plan_id
    assert first.reply_text == "First draft"
    assert second.reply_text == "Second draft"
    assert len(ai.calls) == 2


def test_browser_dm_records_are_frozen() -> None:
    inbound = BrowserInbound("a", "d", "c", "fp", "buyer", "hello", 1)
    reply = BrowserReply(1, "c", "fp", "draft", "engaged")

    with pytest.raises(FrozenInstanceError):
        inbound.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        reply.stage = "closed"  # type: ignore[misc]


def test_second_meaningful_inbound_turn_requests_invitation(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    destination = "WhatsApp: +1 555 0100"
    ai = FakeReplyClient(
        ("Tell me which style you like.", f"Continue on {destination}")
    )
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    first = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-turn-1",
            "buyer",
            "I like this style",
            1,
        )
    )
    second = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-turn-2",
            "buyer",
            "Can you show another one?",
            2,
        )
    )

    assert first.stage == "engaged"
    assert ai.calls[0][1]["should_invite"] is False
    assert second.stage == "invited"
    assert ai.calls[1][1]["should_invite"] is True
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).meaningful_turns
        == 2
    )


def test_atomic_inbound_reservation_has_one_creator_across_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "db.sqlite"
    Database(path).migrate()
    barrier = Barrier(2)

    def reserve(database: Database):
        barrier.wait()
        return database.reserve_browser_inbound_plan(
            "account-01",
            "conversation-01",
            "fp-atomic",
            "buyer",
            "hello",
            1,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in [
                executor.submit(reserve, Database(path)),
                executor.submit(reserve, Database(path)),
            ]
        ]

    assert [created for _, created in results].count(True) == 1
    assert results[0][0] == results[1][0]
    messages = Database(path).recent_web_messages(
        "account-01", "conversation-01", limit=20
    )
    assert [item["message_id"] for item in messages] == ["fp-atomic"]


def test_pending_drafts_reserve_reply_budget(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    for index in range(11):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"inbound-budget-{index}",
            direction="inbound",
            message_type="TEXT",
            text="question",
            timestamp_ms=index * 2,
            participant_username="buyer",
        )
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"outbound-budget-{index}",
            direction="outbound",
            message_type="TEXT",
            text="answer",
            timestamp_ms=index * 2 + 1,
            in_reply_to_message_id=f"inbound-budget-{index}",
        )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations SET auto_reply_count=11
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
    ai = FakeReplyClient(("First pending draft", "Second draft"))
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    first = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-pending-1",
            "buyer",
            "Tell me about this style",
            100,
        )
    )
    second = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-pending-2",
            "buyer",
            "Show me another style",
            101,
        )
    )

    assert first.reply_text == "First pending draft"
    assert second.stage == "closed"
    assert second.reply_text == ""
    assert len(ai.calls) == 1
    assert service.record_result("account-01", "phone-01", first.plan_id, "sent")
    assert not service.record_result("account-01", "phone-01", second.plan_id, "sent")
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).auto_reply_count
        == 12
    )
    assert (
        database.outbound_web_message_count_since(
            "account-01", "conversation-01", since_timestamp_ms=0
        )
        == 12
    )


def test_superseded_pending_draft_releases_budget_without_closing_conversation(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    for index in range(11):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"inbound-release-{index}",
            direction="inbound",
            message_type="TEXT",
            text="question",
            timestamp_ms=index * 2,
            participant_username="buyer",
        )
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"outbound-release-{index}",
            direction="outbound",
            message_type="TEXT",
            text="answer",
            timestamp_ms=index * 2 + 1,
            in_reply_to_message_id=f"inbound-release-{index}",
        )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations SET auto_reply_count=11
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
    ai = FakeReplyClient(("Reserved draft", "Replacement draft"))
    service = BrowserDmService(database, registry_with_browser_account(), ai)

    reserved = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-release-a",
            "buyer",
            "Tell me about this style",
            100,
        )
    )
    blocked = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-release-b",
            "buyer",
            "Show me another style",
            101,
        )
    )

    assert reserved.reply_text == "Reserved draft"
    assert blocked.stage == "closed"
    assert blocked.reply_text == ""
    assert (
        database.browser_conversation_state("account-01", "conversation-01").stage
        != "closed"
    )
    assert service.record_result(
        "account-01", "phone-01", reserved.plan_id, "uncertain"
    )
    assert service.record_result(
        "account-01", "phone-01", reserved.plan_id, "superseded"
    )

    replacement = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-release-c",
            "buyer",
            "Which colors are available?",
            102,
        )
    )

    assert replacement.reply_text == "Replacement draft"
    assert len(ai.calls) == 2
    assert (
        database.browser_conversation_state("account-01", "conversation-01").stage
        != "closed"
    )


def test_contact_at_reply_budget_is_captured_before_closing(tmp_path: Path) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    for index in range(12):
        database.append_web_message(
            "account-01",
            "conversation-01",
            f"outbound-contact-{index}",
            direction="outbound",
            message_type="TEXT",
            text="answer",
            timestamp_ms=index,
            participant_username="buyer",
        )
    ai = FakeReplyClient()
    service = BrowserDmService(
        database,
        registry_with_browser_account(),
        ai,
        clock=lambda: 100.0,
    )

    reply = service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-budget-contact",
            "buyer",
            "My WhatsApp is +44 7700 900123",
            99_000,
        )
    )

    state = database.browser_conversation_state("account-01", "conversation-01")
    assert reply.stage == "closed"
    assert reply.reply_text == ""
    assert ai.calls == []
    assert state.contact_captured_at_ms == 100_000


def test_invitation_evidence_survives_normalization_and_configuration_reload(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "db.sqlite")
    database.migrate()
    configured_destination = "WhatsApp:   +1 555 0100"
    rendered_destination = "WhatsApp: +1 555 0100"
    planning_service = BrowserDmService(
        database,
        registry_with_browser_account(private_channel_hint=configured_destination),
        FakeReplyClient((f"Continue on {rendered_destination}",)),
        clock=lambda: 100.0,
    )

    plan = planning_service.plan(
        BrowserInbound(
            "account-01",
            "phone-01",
            "conversation-01",
            "fp-persisted-invite",
            "buyer",
            "Do you ship?",
            99_000,
        )
    )

    assert plan.stage == "invited"
    with sqlite3.connect(database.path) as connection:
        invitation_included = connection.execute(
            """
            SELECT invitation_included FROM browser_reply_plans WHERE id=?
            """,
            (plan.plan_id,),
        ).fetchone()[0]
    assert invitation_included == 1

    reconciliation_service = BrowserDmService(
        database,
        registry_with_browser_account(
            private_channel_hint="", enabled=False, browser_dm_enabled=False
        ),
        FakeReplyClient(),
        clock=lambda: 101.0,
    )
    assert reconciliation_service.record_result(
        "account-01", "phone-01", plan.plan_id, "sent"
    )
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).last_invited_at_ms
        == 101_000
    )

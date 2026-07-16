from pathlib import Path

from tikpoc.db import Database


def test_web_event_is_deduplicated_and_can_be_claimed_by_account(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    first = database.enqueue_web_event(
        "account-01",
        "dm_received",
        "message-99",
        {"conversation_id": "conversation-1", "text": "hello"},
    )
    duplicate = database.enqueue_web_event(
        "account-01",
        "dm_received",
        "message-99",
        {"conversation_id": "conversation-1", "text": "hello"},
    )

    assert first is True
    assert duplicate is False
    assert database.claim_web_event("account-02") is None
    event = database.claim_web_event("account-01")
    assert event is not None
    assert event.account_id == "account-01"
    assert event.event_type == "dm_received"
    assert event.payload["text"] == "hello"
    assert event.attempts == 1


def test_failed_web_event_retries_and_stale_claim_is_recovered(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.enqueue_web_event("account-01", "dm_received", "message-100", {})

    first = database.claim_web_event()
    assert first is not None
    database.finish_web_event(first.id, False, retry_delay_seconds=0)
    assert database.web_event_state(first.id) == "retry_wait"

    second = database.claim_web_event()
    assert second is not None
    assert database.recover_stale_web_events() == 1
    assert database.web_event_state(second.id) == "retry_wait"


def test_conversation_history_is_deduplicated_and_returned_chronologically(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    assert database.append_web_message(
        "account-01",
        "conversation-1",
        "message-2",
        direction="outbound",
        message_type="TEXT",
        text="second",
        timestamp_ms=2_000,
        participant_id="person-1",
        participant_username="prospect",
        is_follower=True,
        in_reply_to_message_id="message-1",
    )
    assert database.append_web_message(
        "account-01",
        "conversation-1",
        "message-1",
        direction="inbound",
        message_type="TEXT",
        text="first",
        timestamp_ms=1_000,
        participant_id="person-1",
        participant_username="prospect",
        is_follower=True,
    )
    assert not database.append_web_message(
        "account-01",
        "conversation-1",
        "message-1",
        direction="inbound",
        message_type="TEXT",
        text="duplicate",
        timestamp_ms=1_000,
    )

    history = database.recent_web_messages("account-01", "conversation-1", limit=10)

    assert [item["message_id"] for item in history] == ["message-1", "message-2"]
    assert [item["direction"] for item in history] == ["inbound", "outbound"]
    assert database.outbound_web_message_count_since(
        "account-01", "conversation-1", since_timestamp_ms=1_500
    ) == 1
    assert (
        database.web_reply_message_id(
            "account-01", "conversation-1", "message-1"
        )
        == "message-2"
    )


def test_recent_conversation_history_applies_limit_to_latest_messages(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    for index in range(5):
        database.append_web_message(
            "account-01",
            "conversation-1",
            f"message-{index}",
            direction="inbound",
            message_type="TEXT",
            text=str(index),
            timestamp_ms=index,
        )

    history = database.recent_web_messages("account-01", "conversation-1", limit=3)

    assert [item["message_id"] for item in history] == [
        "message-2",
        "message-3",
        "message-4",
    ]

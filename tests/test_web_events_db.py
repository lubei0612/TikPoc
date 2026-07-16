import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier

import pytest

from tikpoc.db import BrowserReplyPlan, Database


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


def test_reply_plan_is_unique_per_account_and_inbound_fingerprint(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    first, created = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "fp-01",
        "prospect",
        "hello",
        1_000,
    )
    second, duplicate_created = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "fp-01",
        "prospect",
        "hello",
        1_000,
    )

    assert created is True
    assert duplicate_created is False
    assert second.id == first.id
    assert second == first


def test_reply_plan_reservation_has_one_creator_across_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.db"
    Database(path).migrate()
    barrier = Barrier(2)

    def reserve(database: Database) -> tuple[BrowserReplyPlan, bool]:
        barrier.wait()
        return database.reserve_browser_reply_plan(
            "account-01",
            "conversation-01",
            "fp-01",
            "prospect",
            "hello",
            1_000,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, Database(path)) for _ in range(2)]
        results = [future.result() for future in futures]

    assert [created for _, created in results].count(True) == 1
    assert results[0][0] == results[1][0]


def test_browser_reply_plan_is_frozen() -> None:
    plan = BrowserReplyPlan(
        id=1,
        account_id="account-01",
        conversation_id="conversation-01",
        inbound_fingerprint="fp-01",
        participant_username="prospect",
        inbound_text="hello",
        inbound_timestamp_ms=1_000,
        reply_text="",
        stage="new",
        state="planning",
    )

    with pytest.raises(FrozenInstanceError):
        plan.state = "planned"  # type: ignore[misc]


def test_browser_storage_migration_is_additive_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE web_conversations (
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                participant_id TEXT NOT NULL DEFAULT '',
                participant_username TEXT NOT NULL DEFAULT '',
                is_follower INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(account_id, conversation_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO web_conversations(account_id, conversation_id)
            VALUES ('account-01', 'conversation-01')
            """
        )

    database = Database(path)
    database.migrate()
    database.migrate()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        conversation_columns = {
            row["name"]: row for row in connection.execute(
                "PRAGMA table_info(web_conversations)"
            )
        }
        row = connection.execute(
            """
            SELECT stage, meaningful_turns, auto_reply_count, last_invited_at_ms,
                   contact_captured_at_ms, human_required
            FROM web_conversations
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        ).fetchone()
        assert row is not None
        assert tuple(row) == ("new", 0, 0, 0, 0, 0)
        expected_columns = {
            "stage": ("TEXT", "'new'"),
            "meaningful_turns": ("INTEGER", "0"),
            "auto_reply_count": ("INTEGER", "0"),
            "last_invited_at_ms": ("INTEGER", "0"),
            "contact_captured_at_ms": ("INTEGER", "0"),
            "human_required": ("INTEGER", "0"),
        }
        for name, (column_type, default) in expected_columns.items():
            assert conversation_columns[name]["type"] == column_type
            assert conversation_columns[name]["notnull"] == 1
            assert conversation_columns[name]["dflt_value"] == default

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"browser_reply_plans", "browser_action_leases"} <= tables
        plan_columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(browser_reply_plans)")
        }
        assert plan_columns["reply_text"]["dflt_value"] == "''"
        assert plan_columns["stage"]["dflt_value"] == "'new'"
        assert plan_columns["state"]["dflt_value"] == "'planning'"
        action_pk = {
            row["name"]: row["pk"]
            for row in connection.execute("PRAGMA table_info(browser_action_leases)")
            if row["pk"]
        }
        assert action_pk == {"account_id": 1, "action_type": 2, "action_key": 3}
        plan_indexes = connection.execute(
            "PRAGMA index_list(browser_reply_plans)"
        ).fetchall()
        indexed_columns = {
            tuple(
                column[2]
                for column in connection.execute(
                    f"PRAGMA index_info({index['name']})"
                )
            )
            for index in plan_indexes
        }
        assert ("account_id", "conversation_id") in indexed_columns
        assert ("account_id", "inbound_fingerprint") in indexed_columns


def test_reply_plan_duplicates_preserve_original_and_are_account_scoped(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    first, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "first", "hello", 1_000
    )
    duplicate, created = database.reserve_browser_reply_plan(
        "account-01", "conversation-other", "fp-01", "changed", "changed", 9_000
    )
    other_account, other_created = database.reserve_browser_reply_plan(
        "account-02", "conversation-02", "fp-01", "second", "hi", 2_000
    )

    assert created is False
    assert duplicate == first
    assert other_created is True
    assert other_account.id != first.id
    assert database.get_browser_reply_plan("account-01", "fp-01") == first
    assert database.get_browser_reply_plan("account-02", "fp-01") == other_account
    assert database.get_browser_reply_plan("account-03", "fp-01") is None
    assert database.browser_reply_plan_by_id(first.id) == first
    assert database.browser_reply_plan_by_id(99_999) is None


@pytest.mark.parametrize(
    ("account_id", "conversation_id", "fingerprint"),
    [
        ("", "conversation-01", "fp-01"),
        ("account-01", "", "fp-01"),
        ("account-01", "conversation-01", ""),
        ("   ", "conversation-01", "fp-01"),
    ],
)
def test_reserve_reply_plan_rejects_empty_identity(
    tmp_path: Path, account_id: str, conversation_id: str, fingerprint: str
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    with pytest.raises(ValueError):
        database.reserve_browser_reply_plan(
            account_id, conversation_id, fingerprint, "prospect", "hello", 1_000
        )


def test_reply_plan_completion_is_exactly_idempotent_and_preserves_draft(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1_000
    )

    first_completion = database.complete_browser_reply_plan(
        plan.id, reply_text="draft reply", stage="qualifying"
    )
    exact_retry = database.complete_browser_reply_plan(
        plan.id, reply_text="draft reply", stage="qualifying"
    )
    conflicting_retry = database.complete_browser_reply_plan(
        plan.id, reply_text="replacement", stage="invited"
    )

    completed = database.browser_reply_plan_by_id(plan.id)
    assert completed is not None
    assert first_completion == completed
    assert exact_retry == completed
    assert conflicting_retry == completed
    assert completed.reply_text == "draft reply"
    assert completed.stage == "qualifying"
    assert completed.state == "planned"


def test_reply_plan_completion_validates_inputs_and_missing_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1_000
    )

    with pytest.raises(ValueError):
        database.complete_browser_reply_plan(
            plan.id, reply_text="", stage="qualifying"
        )
    with pytest.raises(ValueError):
        database.complete_browser_reply_plan(plan.id, reply_text="draft", stage=" ")
    with pytest.raises(KeyError):
        database.complete_browser_reply_plan(
            99_999, reply_text="draft", stage="qualifying"
        )
    with pytest.raises(TypeError):
        database.complete_browser_reply_plan(plan.id, "draft", "qualifying")  # type: ignore[misc]


def test_reply_plan_state_transitions_and_sent_does_not_downgrade(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1_000
    )

    assert database.set_browser_reply_plan_state(plan.id, "uncertain") is None
    stored = database.browser_reply_plan_by_id(plan.id)
    assert stored is not None
    assert stored.state == "planning"

    database.complete_browser_reply_plan(
        plan.id, reply_text="draft", stage="qualifying"
    )
    assert database.set_browser_reply_plan_state(plan.id, "uncertain") is None
    assert database.set_browser_reply_plan_state(plan.id, "uncertain") is None
    assert database.set_browser_reply_plan_state(plan.id, "sent") is None
    assert database.set_browser_reply_plan_state(plan.id, "sent") is None
    assert database.set_browser_reply_plan_state(plan.id, "superseded") is None
    assert database.browser_reply_plan_by_id(plan.id).state == "sent"  # type: ignore[union-attr]
    assert database.set_browser_reply_plan_state(99_999, "sent") is None
    with pytest.raises(ValueError):
        database.set_browser_reply_plan_state(plan.id, "planned")


@pytest.mark.parametrize(
    ("fingerprint", "intermediate_state", "final_state"),
    [
        ("fp-sent", None, "sent"),
        ("fp-superseded", None, "superseded"),
        ("fp-uncertain-superseded", "uncertain", "superseded"),
    ],
)
def test_reply_plan_permitted_terminal_transitions(
    tmp_path: Path,
    fingerprint: str,
    intermediate_state: str | None,
    final_state: str,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    plan, _ = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        fingerprint,
        "prospect",
        "hello",
        1_000,
    )
    database.complete_browser_reply_plan(
        plan.id, reply_text="draft", stage="qualifying"
    )

    if intermediate_state is not None:
        assert (
            database.set_browser_reply_plan_state(plan.id, intermediate_state) is None
        )
    assert database.set_browser_reply_plan_state(plan.id, final_state) is None
    stored = database.browser_reply_plan_by_id(plan.id)
    assert stored is not None
    assert stored.state == final_state


def test_expired_browser_action_lease_can_be_reclaimed(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_000, 30
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", 2_000, 30
    )
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", 32_000, 30
    )


def test_browser_action_same_owner_is_busy_until_expiry_and_accounts_are_scoped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.db"
    database = Database(path)
    database.migrate()

    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_000, 1
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_500, 30
    )
    assert database.claim_browser_action(
        "account-02", "dm_send", "plan-1", "tab-b", 1_500, 30
    )
    with sqlite3.connect(path) as connection:
        expiry = connection.execute(
            """
            SELECT lease_expires_at_ms FROM browser_action_leases
            WHERE account_id='account-01' AND action_type='dm_send'
              AND action_key='plan-1'
            """
        ).fetchone()[0]
    assert expiry == 2_000
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 2_000, 30
    )


def test_browser_action_claim_has_one_winner_across_connections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.db"
    Database(path).migrate()
    barrier = Barrier(2)

    def claim(database: Database, owner_id: str) -> bool:
        barrier.wait()
        return database.claim_browser_action(
            "account-01", "dm_send", "plan-1", owner_id, 1_000, 30
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(claim, Database(path), owner_id)
            for owner_id in ("tab-a", "tab-b")
        ]
        results = [future.result() for future in futures]

    assert results.count(True) == 1


@pytest.mark.parametrize("empty_index", range(4))
def test_browser_action_claim_rejects_empty_identity(
    tmp_path: Path, empty_index: int
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    identities = ["account-01", "dm_send", "plan-1", "tab-a"]
    identities[empty_index] = " "

    with pytest.raises(ValueError):
        database.claim_browser_action(*identities, 1_000, 30)


@pytest.mark.parametrize("empty_index", range(4))
def test_finish_browser_action_rejects_empty_identity(
    tmp_path: Path, empty_index: int
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    identities = ["account-01", "dm_send", "plan-1", "tab-a"]
    identities[empty_index] = " "

    with pytest.raises(ValueError):
        database.finish_browser_action(*identities, "completed")


def test_finish_browser_action_checks_owner_and_completed_is_terminal(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_000, 30
    )

    assert not database.finish_browser_action(
        "account-01", "dm_send", "missing", "tab-a", "completed"
    )
    assert not database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", "completed"
    )
    assert database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", "completed"
    )
    assert database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", "completed"
    )
    assert not database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", "uncertain"
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 99_000, 30
    )
    with pytest.raises(ValueError):
        database.finish_browser_action(
            "account-01", "dm_send", "plan-1", "tab-a", "claimed"
        )


def test_uncertain_browser_action_is_busy_for_same_owner_until_expiry(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_000, 1
    )
    assert database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", "uncertain"
    )

    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_500, 1
    )
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 2_000, 1
    )


def test_uncertain_and_superseded_browser_actions_can_be_reclaimed_after_expiry(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", 1_000, 1
    )
    assert database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-a", "uncertain"
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", 1_500, 1
    )
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", 2_000, 1
    )
    assert database.finish_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", "superseded"
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-b", 2_500, 1
    )
    assert not database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-c", 2_500, 1
    )
    assert database.claim_browser_action(
        "account-01", "dm_send", "plan-1", "tab-c", 3_000, 1
    )

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from threading import Barrier

import pytest

from tikpoc.browser_dm import BrowserDmService
from tikpoc.db import BrowserReplyPlan, Database
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


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
    assert (
        database.outbound_web_message_count_since(
            "account-01", "conversation-1", since_timestamp_ms=1_500
        )
        == 1
    )
    assert (
        database.web_reply_message_id("account-01", "conversation-1", "message-1")
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

    assert plan.invitation_evidence_known is False
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
        connection.executemany(
            """
            INSERT INTO web_conversations(account_id, conversation_id)
            VALUES ('account-01', ?)
            """,
            (
                ("conversation-01",),
                ("conversation-invited-planned",),
                ("conversation-invited-uncertain",),
                ("conversation-invited-ordinary",),
                ("conversation-invited-empty-config",),
                ("conversation-qualifying",),
            ),
        )
        connection.execute(
            """
            CREATE TABLE browser_reply_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                inbound_fingerprint TEXT NOT NULL,
                participant_username TEXT NOT NULL DEFAULT '',
                inbound_text TEXT NOT NULL DEFAULT '',
                inbound_timestamp_ms INTEGER NOT NULL,
                reply_text TEXT NOT NULL DEFAULT '',
                stage TEXT NOT NULL DEFAULT 'new',
                state TEXT NOT NULL DEFAULT 'planning',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, inbound_fingerprint)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, stage, state
            ) VALUES ('account-01', ?, ?, 1000, ?, ?, ?)
            """,
            (
                (
                    "conversation-01",
                    "existing-fingerprint",
                    "existing draft",
                    "engaged",
                    "planned",
                ),
                (
                    "conversation-invited-planned",
                    "invited-planned-fingerprint",
                    "Continue on WhatsApp: +1 555 0100",
                    "invited",
                    "planned",
                ),
                (
                    "conversation-invited-uncertain",
                    "invited-uncertain-fingerprint",
                    "Continue on WhatsApp: +1 555 0100",
                    "invited",
                    "uncertain",
                ),
                (
                    "conversation-invited-ordinary",
                    "invited-ordinary-fingerprint",
                    "The current catalog has several options.",
                    "invited",
                    "planned",
                ),
                (
                    "conversation-invited-empty-config",
                    "invited-empty-config-fingerprint",
                    "Continue on WhatsApp: +1 555 0100",
                    "invited",
                    "planned",
                ),
                (
                    "conversation-qualifying",
                    "qualifying-fingerprint",
                    "Which style do you prefer?",
                    "qualifying",
                    "planned",
                ),
            ),
        )

    database = Database(path)
    database.migrate()
    database.migrate()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        conversation_columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(web_conversations)")
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
        assert plan_columns["invitation_included"]["type"] == "INTEGER"
        assert plan_columns["invitation_included"]["notnull"] == 1
        assert plan_columns["invitation_included"]["dflt_value"] == "0"
        assert plan_columns["invitation_evidence_known"]["type"] == "INTEGER"
        assert plan_columns["invitation_evidence_known"]["notnull"] == 1
        assert plan_columns["invitation_evidence_known"]["dflt_value"] == "0"
        migrated_plan = database.get_browser_reply_plan(
            "account-01", "existing-fingerprint"
        )
        assert migrated_plan is not None
        assert migrated_plan.invitation_included is False
        assert migrated_plan.invitation_evidence_known is False
        invited_planned = database.get_browser_reply_plan(
            "account-01", "invited-planned-fingerprint"
        )
        assert invited_planned is not None
        assert invited_planned.invitation_included is False
        assert invited_planned.invitation_evidence_known is False
        invited_uncertain = database.get_browser_reply_plan(
            "account-01", "invited-uncertain-fingerprint"
        )
        assert invited_uncertain is not None
        assert invited_uncertain.invitation_included is False
        assert invited_uncertain.invitation_evidence_known is False
        invited_ordinary = database.get_browser_reply_plan(
            "account-01", "invited-ordinary-fingerprint"
        )
        assert invited_ordinary is not None
        assert invited_ordinary.invitation_included is False
        assert invited_ordinary.invitation_evidence_known is False
        invited_empty_config = database.get_browser_reply_plan(
            "account-01", "invited-empty-config-fingerprint"
        )
        assert invited_empty_config is not None
        assert invited_empty_config.invitation_included is False
        assert invited_empty_config.invitation_evidence_known is False
        qualifying = database.get_browser_reply_plan(
            "account-01", "qualifying-fingerprint"
        )
        assert qualifying is not None
        assert qualifying.stage == "qualified"
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
                for column in connection.execute(f"PRAGMA index_info({index['name']})")
            )
            for index in plan_indexes
        }
        assert ("account_id", "conversation_id") in indexed_columns
        assert ("account_id", "inbound_fingerprint") in indexed_columns

    service = BrowserDmService(
        database,
        WebAccountRegistry(
            (
                WebAccount(
                    account_id="account-01",
                    device_id="phone-01",
                    mode="browser",
                    private_channel_hint="WhatsApp: +1 555 0100",
                ),
            )
        ),
        object(),
        clock=lambda: 100.0,
    )
    assert service.record_result("account-01", "phone-01", invited_planned.id, "sent")
    assert service.record_result("account-01", "phone-01", invited_uncertain.id, "sent")
    assert service.record_result("account-01", "phone-01", invited_ordinary.id, "sent")
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-invited-planned"
        ).last_invited_at_ms
        == 100_000
    )
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-invited-ordinary"
        ).last_invited_at_ms
        == 0
    )
    reconciled_planned = database.browser_reply_plan_by_id(invited_planned.id)
    reconciled_uncertain = database.browser_reply_plan_by_id(invited_uncertain.id)
    reconciled_ordinary = database.browser_reply_plan_by_id(invited_ordinary.id)
    assert reconciled_planned is not None
    assert reconciled_planned.invitation_included is True
    assert reconciled_planned.invitation_evidence_known is True
    assert reconciled_uncertain is not None
    assert reconciled_uncertain.invitation_included is True
    assert reconciled_uncertain.invitation_evidence_known is True
    assert reconciled_ordinary is not None
    assert reconciled_ordinary.invitation_included is False
    assert reconciled_ordinary.invitation_evidence_known is True

    empty_hint_service = BrowserDmService(
        database,
        WebAccountRegistry(
            (WebAccount(account_id="account-01", device_id="phone-01", mode="browser"),)
        ),
        object(),
        clock=lambda: 101.0,
    )
    assert empty_hint_service.record_result(
        "account-01", "phone-01", invited_empty_config.id, "sent"
    )
    reconciled_empty = database.browser_reply_plan_by_id(invited_empty_config.id)
    assert reconciled_empty is not None
    assert reconciled_empty.invitation_included is False
    assert reconciled_empty.invitation_evidence_known is True
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-invited-empty-config"
        ).last_invited_at_ms
        == 0
    )
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-invited-uncertain"
        ).last_invited_at_ms
        == 100_000
    )


def test_browser_storage_migration_backfills_existing_invitation_column(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    with sqlite3.connect(database.path) as connection:
        connection.row_factory = sqlite3.Row
        plan_columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(browser_reply_plans)")
        }
    assert plan_columns["invitation_evidence_known"]["dflt_value"] == "0"
    assert plan_columns["plan_origin"]["dflt_value"] == "'ai'"
    assert plan_columns["source_inbound_fingerprint"]["dflt_value"] == "''"
    database.append_web_message(
        "account-01",
        "conversation-01",
        "inbound-01",
        direction="inbound",
        message_type="TEXT",
        text="Do you ship?",
        timestamp_ms=1_000,
        participant_username="prospect",
    )
    database.append_web_message(
        "account-01",
        "conversation-known-invitation",
        "inbound-known",
        direction="inbound",
        message_type="TEXT",
        text="Do you ship?",
        timestamp_ms=1_000,
        participant_username="prospect",
    )
    with sqlite3.connect(database.path) as connection:
        connection.executemany(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, stage, state,
                invitation_included, invitation_evidence_known
            ) VALUES ('account-01', ?, ?, 1000, ?, 'invited', ?, ?, ?)
            """,
            (
                (
                    "conversation-01",
                    "legacy-invitation",
                    "Continue on WhatsApp: +1 555 0100",
                    "uncertain",
                    0,
                    0,
                ),
                (
                    "conversation-known-invitation",
                    "known-invitation",
                    "Continue on WhatsApp: +1 555 0100",
                    "planned",
                    1,
                    0,
                ),
            ),
        )

    database.migrate()
    database.migrate()

    migrated = database.get_browser_reply_plan("account-01", "legacy-invitation")
    assert migrated is not None
    assert migrated.invitation_included is False
    assert migrated.invitation_evidence_known is False
    known = database.get_browser_reply_plan("account-01", "known-invitation")
    assert known is not None
    assert known.invitation_included is True
    assert known.invitation_evidence_known is True
    service = BrowserDmService(
        database,
        WebAccountRegistry(
            (
                WebAccount(
                    account_id="account-01",
                    device_id="phone-01",
                    mode="browser",
                    private_channel_hint="WhatsApp: +1 555 0100",
                ),
            )
        ),
        object(),
        clock=lambda: 100.0,
    )
    assert service.record_result("account-01", "phone-01", migrated.id, "sent")
    reconciled = database.browser_reply_plan_by_id(migrated.id)
    assert reconciled is not None
    assert reconciled.invitation_included is True
    assert reconciled.invitation_evidence_known is True
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-01"
        ).last_invited_at_ms
        == 100_000
    )
    reloaded_service = BrowserDmService(
        database,
        WebAccountRegistry(
            (WebAccount(account_id="account-01", device_id="phone-01", mode="browser"),)
        ),
        object(),
        clock=lambda: 101.0,
    )
    assert reloaded_service.record_result("account-01", "phone-01", known.id, "sent")
    assert (
        database.browser_conversation_state(
            "account-01", "conversation-known-invitation"
        ).last_invited_at_ms
        == 101_000
    )


def test_legacy_invitation_evidence_reconciliation_is_one_time_and_durable(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "legacy-reconcile-inbound",
        direction="inbound",
        message_type="TEXT",
        text="Do you ship?",
        timestamp_ms=1_000,
        participant_username="prospect",
    )
    with sqlite3.connect(database.path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, stage, state,
                invitation_included, invitation_evidence_known
            ) VALUES (
                'account-01', 'conversation-01', 'legacy-reconcile',
                1000, 'Continue on WhatsApp: +1 555 0100', 'invited',
                'planned', 0, 0
            )
            """
        )
        plan_id = int(cursor.lastrowid)

    matched = database.reconcile_browser_reply_invitation_evidence(
        "account-01",
        plan_id,
        private_channel_hint="WhatsApp:   +1 555 0100",
    )
    repeated = database.reconcile_browser_reply_invitation_evidence(
        "account-01",
        plan_id,
        private_channel_hint="",
    )

    assert matched.invitation_included is True
    assert matched.invitation_evidence_known is True
    assert repeated == matched


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


def test_reply_plans_store_structured_ai_origin_and_source(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    plan, created = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "buyer", "hello", 1_000
    )

    assert created is True
    assert plan.plan_origin == "ai"
    assert plan.source_inbound_fingerprint == "fp-01"


def test_migration_recovers_legacy_manual_source_and_supersedes_unresolved_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "source:with:delimiters",
        direction="inbound",
        message_type="TEXT",
        text="hello",
        timestamp_ms=1_000,
        participant_username="buyer",
    )
    with sqlite3.connect(database.path) as connection:
        recovered = connection.execute(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, state
            ) VALUES ('account-01', 'conversation-01',
                      'operator-manual:conversation-01:source:with:delimiters',
                      1000, 'legacy reply', 'planned')
            """
        )
        unresolved = connection.execute(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, state
            ) VALUES ('account-01', 'conversation-01',
                      'operator-manual:unresolved', 1000,
                      'unresolved reply', 'planned')
            """
        )
        connection.execute(
            """
            INSERT INTO operator_lead_commands(
                command_type, account_id, conversation_id, command_id, result_json
            ) VALUES ('manual_reply', 'account-01', 'conversation-01', 'legacy', ?)
            """,
            (
                json.dumps(
                    {
                        "account_id": "account-01",
                        "conversation_id": "conversation-01",
                        "plan_id": recovered.lastrowid,
                        "inbound_fingerprint": "source:with:delimiters",
                        "reply_text": "legacy reply",
                        "state": "planned",
                    }
                ),
            ),
        )

    database.migrate()

    recovered_plan = database.browser_reply_plan_by_id(int(recovered.lastrowid))
    unresolved_plan = database.browser_reply_plan_by_id(int(unresolved.lastrowid))
    assert recovered_plan is not None
    assert recovered_plan.plan_origin == "manual"
    assert recovered_plan.source_inbound_fingerprint == "source:with:delimiters"
    assert recovered_plan.state == "planned"
    assert unresolved_plan is not None
    assert unresolved_plan.state == "superseded"


def test_migration_reconciles_legacy_and_structured_manual_plan_collision(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "source-01",
        direction="inbound",
        message_type="TEXT",
        text="hello",
        timestamp_ms=1_000,
        participant_username="buyer",
    )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            UPDATE web_conversations SET stage='human_required', human_required=1
            WHERE account_id='account-01' AND conversation_id='conversation-01'
            """
        )
        connection.execute(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, state, plan_origin,
                source_inbound_fingerprint
            ) VALUES ('account-01', 'conversation-01', 'manual:structured',
                      1000, 'structured reply', 'planned', 'manual', 'source-01')
            """
        )
        legacy = connection.execute(
            """
            INSERT INTO browser_reply_plans(
                account_id, conversation_id, inbound_fingerprint,
                inbound_timestamp_ms, reply_text, state
            ) VALUES ('account-01', 'conversation-01',
                      'operator-manual:conversation-01:source-01',
                      1000, 'legacy reply', 'planned')
            """
        )
        connection.execute(
            """
            INSERT INTO operator_lead_commands(
                command_type, account_id, conversation_id, command_id, result_json
            ) VALUES ('manual_reply', 'account-01', 'conversation-01', 'legacy', ?)
            """,
            (
                json.dumps(
                    {
                        "plan_id": legacy.lastrowid,
                        "inbound_fingerprint": "source-01",
                        "reply_text": "legacy reply",
                    }
                ),
            ),
        )

    database.migrate()

    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM browser_reply_plans
            WHERE account_id='account-01' AND conversation_id='conversation-01'
              AND plan_origin='manual' AND source_inbound_fingerprint='source-01'
              AND state IN ('planning', 'planned', 'uncertain')
            """
            ).fetchone()[0]
            == 1
        )


def test_migration_reconstructs_legacy_operator_request_json(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            INSERT INTO operator_lead_commands(
                command_type, account_id, conversation_id, command_id, result_json
            ) VALUES ('takeover', 'account-01', 'conversation-01', 'legacy', ?)
            """,
            (
                json.dumps(
                    {
                        "account_id": "account-01",
                        "conversation_id": "conversation-01",
                        "stage": "human_required",
                        "human_required": True,
                        "reason": "operator",
                    }
                ),
            ),
        )

    database.migrate()

    with sqlite3.connect(database.path) as connection:
        request_json = connection.execute(
            "SELECT request_json FROM operator_lead_commands WHERE command_id='legacy'"
        ).fetchone()[0]
    assert json.loads(request_json) == {"reason": "operator"}


def test_atomic_dm_claim_has_one_winner_and_rejects_aliases(tmp_path: Path) -> None:
    path = tmp_path / "tasks.db"
    database = Database(path)
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "message-01",
        direction="inbound",
        message_type="TEXT",
        text="hello",
        timestamp_ms=1_000,
        participant_username="buyer",
    )
    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "message-01", "buyer", "hello", 1_000
    )
    database.complete_browser_reply_plan(plan.id, reply_text="reply", stage="engaged")
    aliases = (str(plan.id), f"dm_send:+{plan.id}", f"dm_send:0{plan.id}")

    for alias in aliases:
        assert not database.claim_browser_dm_action(
            "account-01",
            alias,
            "alias-owner",
            1_000,
            30,
            default_ai_enabled=True,
        )

    barrier = Barrier(2)

    def claim(owner_id: str) -> bool:
        barrier.wait()
        return Database(path).claim_browser_dm_action(
            "account-01",
            f"dm_send:{plan.id}",
            owner_id,
            1_000,
            30,
            default_ai_enabled=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [
            future.result()
            for future in (
                executor.submit(claim, "tab-a"),
                executor.submit(claim, "tab-b"),
            )
        ]

    assert results.count(True) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT action_key FROM browser_action_leases"
        ).fetchall() == [(f"dm_send:{plan.id}",)]


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
    assert completed.stage == "qualified"
    assert completed.state == "planned"


def test_legacy_qualifying_completion_records_canonical_sent_result(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-01",
        "fp-qualifying",
        direction="inbound",
        message_type="TEXT",
        text="Which style?",
        timestamp_ms=1_000,
        participant_username="prospect",
    )
    plan, _ = database.reserve_browser_reply_plan(
        "account-01",
        "conversation-01",
        "fp-qualifying",
        "prospect",
        "Which style?",
        1_000,
    )

    completed = database.complete_browser_reply_plan(
        plan.id, reply_text="This style is available.", stage="qualifying"
    )
    assert completed.stage == "qualified"
    assert database.record_browser_reply_result(
        "account-01", completed.id, "sent", now_ms=2_000
    )

    state = database.browser_conversation_state("account-01", "conversation-01")
    messages = database.recent_web_messages("account-01", "conversation-01", limit=20)
    assert state.stage == "qualified"
    assert state.auto_reply_count == 1
    assert [message["direction"] for message in messages] == ["inbound", "outbound"]


def test_reply_plan_completion_validates_inputs_and_missing_plan(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    plan, _ = database.reserve_browser_reply_plan(
        "account-01", "conversation-01", "fp-01", "prospect", "hello", 1_000
    )

    with pytest.raises(ValueError):
        database.complete_browser_reply_plan(plan.id, reply_text="", stage="qualifying")
    with pytest.raises(ValueError):
        database.complete_browser_reply_plan(plan.id, reply_text="draft", stage=" ")
    with pytest.raises(ValueError, match="invalid.*stage"):
        database.complete_browser_reply_plan(
            plan.id, reply_text="draft", stage="unsupported-stage"
        )
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


def test_lead_funnel_sales_health_and_latency_read_models(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    for stage in (
        "dm_inbound",
        "engaged",
        "qualified",
        "invited",
        "contact_captured",
        "human_required",
    ):
        assert database.record_lead_funnel_event(
            "account-01",
            "buyer",
            stage,
            "fp-01",
            conversation_id="conversation-01",
            occurred_at_ms=1_000,
        )
    assert not database.record_lead_funnel_event(
        "account-01",
        "buyer",
        "contact_captured",
        "fp-01",
        conversation_id="conversation-01",
        occurred_at_ms=2_000,
    )
    assert database.lead_funnel_snapshot() == {
        "dm_inbound": 1,
        "engaged": 1,
        "qualified": 1,
        "invited": 1,
        "contact_captured": 1,
        "human_required": 1,
    }
    recent = database.recent_leads(limit=1)[0]
    assert {
        "account_id": "account-01",
        "participant_username": "buyer",
        "conversation_id": "conversation-01",
        "stage": "human_required",
    }.items() <= recent.items()
    assert database.record_lead_funnel_event(
        "account-01",
        "buyer",
        "engaged",
        "fp-later",
        conversation_id="conversation-01",
        occurred_at_ms=2_500,
    )
    recent = database.recent_leads(limit=1)[0]
    assert recent["stage"] == "human_required"
    assert recent["occurred_at_ms"] == 2_500

    sale_id = database.record_lead_sale(
        "account-01",
        "buyer",
        amount_minor=12_345,
        currency="USD",
        status="confirmed",
        occurred_at_ms=3_000,
    )
    assert sale_id > 0
    assert database.lead_sales_snapshot() == {
        "by_status": {"confirmed": 1},
        "confirmed_revenue_minor": {"USD": 12_345},
        "sales": 1,
    }

    database.upsert_browser_health(
        "account-01",
        "messages",
        device_id="phone-01",
        status="ready",
        observed_at_ms=4_000,
        detail="Messages visible",
    )
    assert database.browser_health_snapshot() == [
        {
            "account_id": "account-01",
            "page_role": "messages",
            "device_id": "phone-01",
            "status": "ready",
            "observed_at_ms": 4_000,
            "detail": "Messages visible",
        }
    ]

    first, _ = database.reserve_browser_inbound_plan(
        "account-01", "conversation-01", "latency-1", "buyer", "hello", 1_000
    )
    second, _ = database.reserve_browser_inbound_plan(
        "account-01", "conversation-01", "latency-2", "buyer", "again", 2_000
    )
    for plan, sent_at_ms in ((first, 1_100), (second, 2_900)):
        database.complete_browser_reply_plan(
            plan.id, reply_text="reply", stage="engaged"
        )
        with sqlite3.connect(database.path) as connection:
            connection.execute(
                "UPDATE browser_reply_plans SET created_at_ms=? WHERE id=?",
                (1_000 if plan.id == first.id else 2_000, plan.id),
            )
        assert database.record_browser_reply_result(
            "account-01", plan.id, "sent", now_ms=sent_at_ms
        )
    assert database.reply_latency_snapshot() == {
        "confirmed_replies": 2,
        "median_ms": 500,
        "p90_ms": 900,
    }


@pytest.mark.parametrize(
    ("amount_minor", "currency", "status"),
    ((0, "USD", "confirmed"), (100, "usd", "confirmed"), (100, "USD", "paid")),
)
def test_lead_sale_rejects_invalid_business_values(
    tmp_path: Path, amount_minor: int, currency: str, status: str
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    with pytest.raises(ValueError):
        database.record_lead_sale(
            "account-01",
            "buyer",
            amount_minor=amount_minor,
            currency=currency,
            status=status,
            occurred_at_ms=1_000,
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

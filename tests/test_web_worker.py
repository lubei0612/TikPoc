import sqlite3
from pathlib import Path

import pytest

from tikpoc import web_worker as web_worker_module
from tikpoc.db import Database
from tikpoc.models import TaskState
from tikpoc.web_accounts import WebAccount, WebAccountRegistry
from tikpoc.web_worker import WebEventWorker, run_web_queue


class FakeReplyClient:
    def __init__(self) -> None:
        self.calls = []

    def reply_conversation(self, history, **kwargs) -> str:
        self.calls.append((history, kwargs))
        return "Thanks. Continue on WhatsApp: example"


class FakeMessagingClient:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    def send_sender_action(self, conversation_id: str, action: str) -> None:
        self.actions.append((conversation_id, action))

    def send_text(
        self,
        conversation_id: str,
        text: str,
        *,
        referenced_message_id: str = "",
    ) -> str:
        self.messages.append((conversation_id, text, referenced_message_id))
        return "outbound-1"


def _registry(tmp_path: Path) -> WebAccountRegistry:
    return WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                business_id="business-01",
                token_file=tmp_path / "token.json",
                private_channel_hint="Continue on WhatsApp: example",
            ),
        )
    )


def _payload() -> dict[str, object]:
    return {
        "business_id": "business-01",
        "conversation_id": "conversation-1",
        "message_id": "inbound-1",
        "sender_id": "person-1",
        "sender_username": "prospect",
        "text": "Can you tell me more?",
        "message_type": "TEXT",
        "timestamp_ms": 1_000,
        "is_follower": True,
        "source": "WEB",
    }


def test_web_worker_replies_without_claiming_mobile_task(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "target-1", "sample", "phone-01")
    database.enqueue_web_event(
        "account-01", "dm_received", "inbound-1", _payload()
    )
    reply_client = FakeReplyClient()
    messaging_client = FakeMessagingClient()
    worker = WebEventWorker(
        database,
        _registry(tmp_path),
        reply_client,
        client_factory=lambda account: messaging_client,
        clock=lambda: 2.0,
    )

    assert worker.run_one() is True

    assert database.task_state(task_id) == TaskState.PENDING
    assert database.web_event_state(1) == "completed"
    history = database.recent_web_messages("account-01", "conversation-1")
    assert [message["message_id"] for message in history] == [
        "inbound-1",
        "outbound-1",
    ]
    assert messaging_client.actions == [
        ("conversation-1", "TYPING"),
        ("conversation-1", "MARK_READ"),
    ]
    assert messaging_client.messages == [
        (
            "conversation-1",
            "Thanks. Continue on WhatsApp: example",
            "inbound-1",
        )
    ]
    _, reply_options = reply_client.calls[0]
    assert reply_options["private_channel_hint"] == "Continue on WhatsApp: example"


def test_web_worker_does_not_resend_when_reply_is_already_recorded(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.append_web_message(
        "account-01",
        "conversation-1",
        "inbound-1",
        direction="inbound",
        message_type="TEXT",
        text="hello",
        timestamp_ms=1_000,
    )
    database.append_web_message(
        "account-01",
        "conversation-1",
        "outbound-existing",
        direction="outbound",
        message_type="TEXT",
        text="already replied",
        timestamp_ms=2_000,
        in_reply_to_message_id="inbound-1",
    )
    database.enqueue_web_event(
        "account-01", "dm_received", "inbound-1", _payload()
    )
    messaging_client = FakeMessagingClient()
    worker = WebEventWorker(
        database,
        _registry(tmp_path),
        FakeReplyClient(),
        client_factory=lambda account: messaging_client,
    )

    assert worker.run_one() is True
    assert messaging_client.messages == []
    assert database.web_event_state(1) == "completed"


def test_web_worker_enforces_ten_message_window_limit(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    for index in range(10):
        database.append_web_message(
            "account-01",
            "conversation-1",
            f"outbound-{index}",
            direction="outbound",
            message_type="TEXT",
            text="reply",
            timestamp_ms=1_000 + index,
        )
    database.enqueue_web_event(
        "account-01", "dm_received", "inbound-1", _payload()
    )
    messaging_client = FakeMessagingClient()
    worker = WebEventWorker(
        database,
        _registry(tmp_path),
        FakeReplyClient(),
        client_factory=lambda account: messaging_client,
    )

    assert worker.run_one() is True
    assert messaging_client.messages == []
    assert database.web_event_state(1) == "failed"


def test_web_worker_returns_false_when_queue_is_empty(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    worker = WebEventWorker(
        database,
        _registry(tmp_path),
        FakeReplyClient(),
        client_factory=lambda account: FakeMessagingClient(),
    )

    assert worker.run_one() is False


def test_web_worker_keeps_running_when_token_file_is_missing(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.enqueue_web_event(
        "account-01", "dm_received", "inbound-1", _payload()
    )
    reply_client = FakeReplyClient()
    worker = WebEventWorker(database, _registry(tmp_path), reply_client)

    assert worker.run_one() is True
    assert database.web_event_state(1) == "retry_wait"
    assert reply_client.calls == []


def test_run_web_queue_retries_transient_database_open_errors(
    tmp_path: Path, monkeypatch
) -> None:
    sleeps: list[float] = []

    class FakeDatabase:
        def __init__(self, path: Path) -> None:
            self.path = path

        def migrate(self) -> None:
            return

        def recover_stale_web_events(self) -> int:
            return 0

    class FakeWorker:
        calls = 0

        def __init__(self, *args, **kwargs) -> None:
            return

        def run_one(self) -> bool:
            self.calls += 1
            if self.calls == 1:
                raise sqlite3.OperationalError("unable to open database file")
            raise KeyboardInterrupt

    monkeypatch.setattr(web_worker_module, "Database", FakeDatabase)
    monkeypatch.setattr(web_worker_module, "WebEventWorker", FakeWorker)
    monkeypatch.setattr(web_worker_module.time, "sleep", sleeps.append)

    with pytest.raises(KeyboardInterrupt):
        run_web_queue(
            tmp_path / "tasks.db",
            registry=_registry(tmp_path),
            idle_sleep_seconds=0.25,
            reply_client=FakeReplyClient(),
        )

    assert sleeps == [1.0]

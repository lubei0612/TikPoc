import sqlite3
from pathlib import Path

import pytest

from tikpoc.db import Database
from tikpoc.models import ProfileMetrics, TaskState


def test_database_enables_wal_and_long_busy_timeout(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    with database._connect() as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 30_000


def test_database_connection_context_closes_the_connection(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    with database._connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_claim_next_moves_oldest_task_to_running(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    first_id = database.insert_task("batch", "1", "first")
    database.insert_task("batch", "2", "second")

    task = database.claim_next()

    assert task is not None
    assert task.id == first_id
    assert task.username == "first"
    assert database.task_state(first_id) == TaskState.RUNNING


def test_recover_stale_running_task_returns_it_to_retry_wait(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "1", "sample")
    database.claim_next()

    recovered = database.recover_stale_tasks()

    assert recovered == 1
    assert database.task_state(task_id) == TaskState.RETRY_WAIT


def test_completed_task_is_not_claimed_again(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "1", "sample")
    database.claim_next()
    database.finish(task_id, TaskState.COMPLETED)

    assert database.claim_next() is None


def test_target_is_assigned_once_to_every_enabled_device(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    ids = database.assign_target_to_devices(
        "batch", "target-1", "sample", ("phone-01", "phone-02", "phone-03")
    )

    assert len(ids) == 3
    assert database.claim_next("phone-01").device_id == "phone-01"
    assert database.claim_next("phone-02").device_id == "phone-02"
    assert database.claim_next("phone-03").device_id == "phone-03"


def test_task_claim_preserves_prescreened_profile_snapshot(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.insert_task(
        "batch",
        "target-1",
        "sample",
        "phone-01",
        profile_metrics=ProfileMetrics(20, 10, 5),
        private_account=False,
        sec_uid="sec-1",
        profile_url="https://www.tiktok.com/@sample",
    )

    task = database.claim_next("phone-01")

    assert task is not None
    assert task.profile_metrics == ProfileMetrics(20, 10, 5)
    assert task.private_account is False
    assert task.sec_uid == "sec-1"
    assert task.profile_url == "https://www.tiktok.com/@sample"


def test_device_event_is_deduplicated_and_claimed_by_its_device(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    first = database.enqueue_device_event(
        "phone-01", "dm_received", "message-99", {"username": "sample"}
    )
    duplicate = database.enqueue_device_event(
        "phone-01", "dm_received", "message-99", {"username": "sample"}
    )

    assert first is True
    assert duplicate is False
    assert database.claim_device_event("phone-02") is None
    event = database.claim_device_event("phone-01")
    assert event is not None
    assert event.event_type == "dm_received"
    assert event.payload == {"username": "sample"}


def test_failed_device_event_retries_before_terminal_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.enqueue_device_event(
        "phone-01", "dm_received", "message-100", {"message": "hello"}
    )

    first = database.claim_device_event("phone-01")
    assert first is not None
    assert first.attempts == 1
    database.finish_device_event(first.id, False, retry_delay_seconds=0)
    assert database.device_event_state(first.id) == "retry_wait"

    second = database.claim_device_event("phone-01")
    assert second is not None
    assert second.attempts == 2
    database.finish_device_event(second.id, False, retry_delay_seconds=0)

    third = database.claim_device_event("phone-01")
    assert third is not None
    assert third.attempts == 3
    database.finish_device_event(third.id, False, retry_delay_seconds=0)
    assert database.device_event_state(third.id) == "failed"


def test_stale_running_device_event_is_recovered(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.enqueue_device_event("phone-01", "new_follower", "follow-1", {})
    event = database.claim_device_event("phone-01")
    assert event is not None

    assert database.recover_stale_device_events() == 1
    assert database.device_event_state(event.id) == "retry_wait"
    assert database.claim_device_event("phone-01") is not None

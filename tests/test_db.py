from pathlib import Path

from tikpoc.db import Database
from tikpoc.models import TaskState


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

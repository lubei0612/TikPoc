from pathlib import Path

from tikpoc.db import Database
from tikpoc.models import TaskState


def test_dashboard_snapshot_and_recent_tasks(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    first = database.insert_task("batch", "1", "first")
    database.insert_task("batch", "2", "second")
    database.claim_next()
    database.checkpoint(first, "metrics_reading")
    database.finish(first, TaskState.COMPLETED)

    snapshot = database.dashboard_snapshot()
    recent = database.recent_tasks(2)

    assert snapshot["total"] == 2
    assert snapshot["processed"] == 1
    assert snapshot["counts"] == {"completed": 1, "pending": 1}
    assert recent[0]["username"] == "first"
    assert recent[0]["checkpoint"] == "metrics_reading"


def test_worker_control_and_runtime_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()

    assert database.worker_control() == "running"
    database.set_worker_control("paused")
    database.record_runtime_event("worker_paused", "sample")

    assert database.worker_control() == "paused"
    assert database.latest_runtime_event()["event_type"] == "worker_paused"

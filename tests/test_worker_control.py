from pathlib import Path

from tests.fakes import FakeDevice
from tikpoc.db import Database
from tikpoc.models import ProfileMetrics, TaskState
from tikpoc.worker import Worker


def _setup(tmp_path: Path) -> tuple[Database, FakeDevice, int]:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "1", "sample")
    device = FakeDevice(metrics=ProfileMetrics(20, 10, 5), posts=("a",))
    return database, device, task_id


def test_pause_prevents_claim_and_resume_allows_it(tmp_path: Path) -> None:
    database, device, task_id = _setup(tmp_path)
    worker = Worker(database, device)
    database.set_worker_control("paused")

    assert worker.run_one() is False
    assert database.task_state(task_id) == TaskState.PENDING
    assert device.opened_profiles == []

    database.set_worker_control("running")
    assert worker.run_one() is True
    assert database.task_state(task_id) == TaskState.COMPLETED


def test_stop_prevents_another_claim(tmp_path: Path) -> None:
    database, device, task_id = _setup(tmp_path)
    database.set_worker_control("stopped")

    assert Worker(database, device).run_one() is False
    assert database.task_state(task_id) == TaskState.PENDING
    assert database.latest_runtime_event()["event_type"] == "worker_stopped"

from pathlib import Path

from tests.fakes import FakeDevice
from tikpoc.db import Database
from tikpoc.models import ProfileMetrics, TaskState
from tikpoc.worker import Worker


def test_worker_opens_one_random_post_for_eligible_profile(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "1", "sample")
    device = FakeDevice(metrics=ProfileMetrics(20, 10, 5), posts=("a", "b", "c"))

    Worker(database, device, random_seed=7).run_one()

    assert database.task_state(task_id) == TaskState.COMPLETED
    assert device.opened_profiles == ["sample"]
    assert device.opened_posts[0] in {"a", "b", "c"}


def test_worker_skips_profile_that_fails_rule(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    task_id = database.insert_task("batch", "1", "sample")
    device = FakeDevice(metrics=ProfileMetrics(10, 20, 5), posts=("a",))

    Worker(database, device, random_seed=7).run_one()

    assert database.task_state(task_id) == TaskState.SKIPPED
    assert device.opened_posts == []

from pathlib import Path

from tests.fakes import FakeDevice
from tikpoc.db import Database
from tikpoc.models import ProfileMetrics, TaskState
from tikpoc.interactions import ActionPolicy, InteractionPolicy
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


def test_worker_performs_configured_actions_on_selected_post(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.insert_task("batch", "1", "sample")
    device = FakeDevice(metrics=ProfileMetrics(20, 10, 5), posts=("a",))
    policy = InteractionPolicy(
        like=ActionPolicy(True, 1.0, 5),
        trace_probability=0.0,
    )

    Worker(database, device, interaction_policy=policy, random_seed=7).run_one()

    assert device.actions == ["like"]


def test_event_worker_can_suppress_repeated_empty_queue_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    worker = Worker(database, FakeDevice(metrics=ProfileMetrics(0, 0, 0), posts=()))

    assert worker.run_one(record_empty=False) is False
    assert database.latest_runtime_event() is None


def test_worker_reuses_prescreened_metrics_instead_of_reading_them_again(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "tasks.db")
    database.migrate()
    database.insert_task(
        "batch",
        "1",
        "sample",
        profile_metrics=ProfileMetrics(20, 10, 5),
        private_account=False,
    )
    device = FakeDevice(metrics=ProfileMetrics(0, 0, 0), posts=("a",))

    Worker(database, device, random_seed=7).run_one()

    assert device.waited_profiles == ["sample"]
    assert device.metric_reads == 0
    assert device.opened_posts == ["a"]

import random

from .db import Database
from .device import Device
from .models import TaskState
from .rules import evaluate_profile


class Worker:
    def __init__(
        self,
        database: Database,
        device: Device,
        *,
        random_seed: int | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.database = database
        self.device = device
        self.random = random.Random(random_seed)
        self.max_attempts = max_attempts

    def run_one(self) -> bool:
        control = self.database.worker_control()
        if control != "running":
            self.database.record_runtime_event(f"worker_{control}")
            return False
        task = self.database.claim_next()
        if task is None:
            self.database.record_runtime_event("queue_empty")
            return False
        try:
            self.database.record_runtime_event("device_ready", task.username)
            self.device.ensure_ready()
            self.database.record_runtime_event("profile_opening", task.username)
            self.device.open_profile(task.username)
            self.database.checkpoint(task.id, "profile_opened")
            self.database.record_runtime_event("metrics_reading", task.username)
            decision = evaluate_profile(self.device.read_profile_metrics())
            if not decision.eligible:
                self.database.finish(task.id, TaskState.SKIPPED, decision.reasons[0])
                self.database.record_runtime_event("task_skipped", task.username)
                return True
            posts = self.device.list_visible_posts()
            if not posts:
                self.database.finish(task.id, TaskState.SKIPPED, "no_eligible_posts")
                self.database.record_runtime_event("task_skipped", task.username)
                return True
            post_id = self.random.choice(posts)
            self.database.record_runtime_event("post_opening", task.username)
            self.device.open_post(post_id)
            self.database.checkpoint(task.id, f"post_opened:{post_id}")
            self.device.return_to_baseline()
            self.database.finish(task.id, TaskState.COMPLETED)
            self.database.record_runtime_event("task_completed", task.username)
        except Exception as error:
            state = (
                TaskState.FAILED
                if task.attempts >= self.max_attempts
                else TaskState.RETRY_WAIT
            )
            self.database.finish(task.id, state, type(error).__name__)
            self.database.record_runtime_event(state.value, task.username)
        return True

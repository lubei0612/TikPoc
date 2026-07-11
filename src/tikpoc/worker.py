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
        task = self.database.claim_next()
        if task is None:
            return False
        try:
            self.device.ensure_ready()
            self.device.open_profile(task.username)
            self.database.checkpoint(task.id, "profile_opened")
            decision = evaluate_profile(self.device.read_profile_metrics())
            if not decision.eligible:
                self.database.finish(task.id, TaskState.SKIPPED, decision.reasons[0])
                return True
            posts = self.device.list_visible_posts()
            if not posts:
                self.database.finish(task.id, TaskState.SKIPPED, "no_eligible_posts")
                return True
            post_id = self.random.choice(posts)
            self.device.open_post(post_id)
            self.database.checkpoint(task.id, f"post_opened:{post_id}")
            self.device.return_to_baseline()
            self.database.finish(task.id, TaskState.COMPLETED)
        except Exception as error:
            state = (
                TaskState.FAILED
                if task.attempts >= self.max_attempts
                else TaskState.RETRY_WAIT
            )
            self.database.finish(task.id, state, type(error).__name__)
        return True

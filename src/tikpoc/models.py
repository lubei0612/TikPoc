from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    RETRY_WAIT = "retry_wait"
    FAILED = "failed"


@dataclass(frozen=True)
class ProfileMetrics:
    following: int
    followers: int
    posts: int

    def __post_init__(self) -> None:
        if min(self.following, self.followers, self.posts) < 0:
            raise ValueError("profile metrics must be nonnegative")

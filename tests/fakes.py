from dataclasses import dataclass, field

from tikpoc.models import ProfileMetrics


@dataclass
class FakeDevice:
    metrics: ProfileMetrics
    posts: tuple[str, ...]
    opened_profiles: list[str] = field(default_factory=list)
    waited_profiles: list[str] = field(default_factory=list)
    opened_posts: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    metric_reads: int = 0

    def ensure_ready(self) -> None:
        return None

    def open_profile(self, username: str) -> None:
        self.opened_profiles.append(username)

    def wait_profile_ready(self, username: str) -> None:
        self.waited_profiles.append(username)

    def read_profile_metrics(self) -> ProfileMetrics:
        self.metric_reads += 1
        return self.metrics

    def list_visible_posts(self) -> tuple[str, ...]:
        return self.posts

    def open_post(self, post_id: str) -> None:
        self.opened_posts.append(post_id)

    def perform_action(self, action: str) -> bool:
        self.actions.append(action)
        return True

    def return_to_baseline(self) -> None:
        return None

    def restart_app(self) -> None:
        return None

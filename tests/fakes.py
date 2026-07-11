from dataclasses import dataclass, field

from tikpoc.models import ProfileMetrics


@dataclass
class FakeDevice:
    metrics: ProfileMetrics
    posts: tuple[str, ...]
    opened_profiles: list[str] = field(default_factory=list)
    opened_posts: list[str] = field(default_factory=list)

    def ensure_ready(self) -> None:
        return None

    def open_profile(self, username: str) -> None:
        self.opened_profiles.append(username)

    def read_profile_metrics(self) -> ProfileMetrics:
        return self.metrics

    def list_visible_posts(self) -> tuple[str, ...]:
        return self.posts

    def open_post(self, post_id: str) -> None:
        self.opened_posts.append(post_id)

    def return_to_baseline(self) -> None:
        return None

    def restart_app(self) -> None:
        return None


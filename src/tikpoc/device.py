import time
from typing import Protocol

from selenium.webdriver.common.by import By

from .models import ProfileMetrics
from .profile_parser import parse_profile_page, parse_visible_post_keys


TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
POST_CONTAINER_ID = f"{TIKTOK_PACKAGE}:id/eqx"


class Device(Protocol):
    def ensure_ready(self) -> None: ...

    def open_profile(self, username: str) -> None: ...

    def read_profile_metrics(self) -> ProfileMetrics: ...

    def list_visible_posts(self) -> tuple[str, ...]: ...

    def open_post(self, post_id: str) -> None: ...

    def return_to_baseline(self) -> None: ...

    def restart_app(self) -> None: ...


class AppiumTikTokDevice:
    def __init__(
        self,
        driver: object,
        *,
        metric_read_attempts: int = 20,
        poll_interval: float = 0.5,
    ) -> None:
        self.driver = driver
        self.metric_read_attempts = metric_read_attempts
        self.poll_interval = poll_interval

    def ensure_ready(self) -> None:
        self.driver.activate_app(TIKTOK_PACKAGE)

    def open_profile(self, username: str) -> None:
        normalized = username.strip().removeprefix("@").lower()
        self.driver.execute_script(
            "mobile: deepLink",
            {
                "url": f"https://www.tiktok.com/@{normalized}",
                "package": TIKTOK_PACKAGE,
            },
        )

    def read_profile_metrics(self) -> ProfileMetrics:
        last_error: ValueError | None = None
        for attempt in range(self.metric_read_attempts):
            try:
                page = parse_profile_page(self.driver.page_source)
                if page.visible_post_count != 3:
                    return page.metrics
                self.driver.swipe(540, 1900, 540, 1050, 600)
                time.sleep(self.poll_interval)
                next_keys = parse_visible_post_keys(self.driver.page_source)
                has_new_post = bool(set(next_keys) - set(page.visible_post_keys))
                return ProfileMetrics(
                    following=page.metrics.following,
                    followers=page.metrics.followers,
                    posts=4 if has_new_post else 3,
                )
            except ValueError as error:
                last_error = error
                if attempt + 1 < self.metric_read_attempts:
                    time.sleep(self.poll_interval)
        raise last_error or ValueError("profile metrics are incomplete")

    def list_visible_posts(self) -> tuple[str, ...]:
        elements = self.driver.find_elements(By.ID, POST_CONTAINER_ID)
        return tuple(str(index) for index in range(len(elements)))

    def open_post(self, post_id: str) -> None:
        elements = self.driver.find_elements(By.ID, POST_CONTAINER_ID)
        index = int(post_id)
        if index < 0 or index >= len(elements):
            raise ValueError(f"post is no longer visible: {post_id}")
        elements[index].click()

    def return_to_baseline(self) -> None:
        self.driver.back()

    def restart_app(self) -> None:
        self.driver.terminate_app(TIKTOK_PACKAGE)
        self.driver.activate_app(TIKTOK_PACKAGE)

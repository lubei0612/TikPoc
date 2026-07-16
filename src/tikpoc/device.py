import time
from collections.abc import Callable
from typing import Protocol

from selenium.webdriver.common.by import By

from .models import ProfileMetrics
from .profile_parser import parse_profile_page, parse_visible_post_keys


TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
POST_CONTAINER_ID = f"{TIKTOK_PACKAGE}:id/eqx"
PROFILE_USERNAME_ID = f"{TIKTOK_PACKAGE}:id/s7e"


class Device(Protocol):
    def ensure_ready(self) -> None: ...

    def open_profile(self, username: str) -> None: ...

    def wait_profile_ready(self, username: str) -> None: ...

    def read_profile_metrics(self) -> ProfileMetrics: ...

    def list_visible_posts(self) -> tuple[str, ...]: ...

    def open_post(self, post_id: str) -> None: ...

    def perform_action(self, action: str) -> bool: ...

    def return_to_baseline(self) -> None: ...

    def restart_app(self) -> None: ...

    def follow_back_new_followers(self, limit: int = 10) -> int: ...

    def greet_one_new_follower(self, reply_provider: Callable[[str], str]) -> bool: ...

    def reply_to_inbox_event(
        self, title: str, message: str, reply_provider: Callable[[str], str]
    ) -> bool: ...


class AppiumTikTokDevice:
    def __init__(
        self,
        driver: object,
        *,
        metric_read_attempts: int = 20,
        poll_interval: float = 0.5,
        action_delay: float = 1.0,
    ) -> None:
        self.driver = driver
        self.metric_read_attempts = metric_read_attempts
        self.poll_interval = poll_interval
        self.action_delay = action_delay

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

    def wait_profile_ready(self, username: str) -> None:
        normalized = username.strip().removeprefix("@").lower()
        last_error: ValueError | None = None
        for attempt in range(self.metric_read_attempts):
            if attempt and attempt % 3 == 0:
                self.open_profile(normalized)
            elements = self.driver.find_elements(By.ID, PROFILE_USERNAME_ID)
            if elements:
                actual = str(elements[0].text or "").strip().removeprefix("@").lower()
                if actual == normalized:
                    return
                if actual:
                    last_error = ValueError(
                        f"profile mismatch: expected {normalized}, got {actual}"
                    )
            else:
                last_error = ValueError("profile username marker is not visible")
            if attempt + 1 < self.metric_read_attempts:
                time.sleep(self.poll_interval)
        raise last_error or ValueError("profile did not become ready")

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

    def perform_action(self, action: str) -> bool:
        selectors = {
            "like": '//*[starts-with(@content-desc, "Like video.")]',
            "favorite": (
                '//*[@content-desc="Add or remove this video from Favorites."]/..'
            ),
            "share": '//*[starts-with(@content-desc, "Share video.")]',
        }
        try:
            selector = selectors[action]
        except KeyError as error:
            raise ValueError(f"unsupported action: {action}") from error
        element = self.driver.find_element(By.XPATH, selector)
        before_image = element.screenshot_as_png if action == "favorite" else None
        element.click()
        if action == "share":
            time.sleep(self.action_delay)
            self.driver.find_element(
                By.XPATH, '//*[@text="Repost" or @content-desc="Repost"]'
            ).click()
        time.sleep(self.action_delay)
        if action == "like" and not self.driver.find_elements(
            By.XPATH, '//*[@content-desc="Video liked"]'
        ):
            raise RuntimeError("like action was not verified")
        if action == "favorite":
            after_image = element.screenshot_as_png
            if before_image == after_image:
                raise RuntimeError("favorite action was not verified")
        if action == "share" and not self.driver.find_elements(
            By.XPATH,
            '//*[contains(@text,"You reposted") or contains(@content-desc,"You reposted")]',
        ):
            raise RuntimeError("share action was not verified")
        return True

    def return_to_baseline(self) -> None:
        self.driver.back()

    def restart_app(self) -> None:
        self.driver.terminate_app(TIKTOK_PACKAGE)
        self.driver.activate_app(TIKTOK_PACKAGE)

    def follow_back_new_followers(self, limit: int = 10) -> int:
        if not self._open_new_followers():
            return 0
        buttons = self.driver.find_elements(By.XPATH, '//*[@text="Follow back"]')
        count = 0
        for button in buttons[: max(0, limit)]:
            button.click()
            count += 1
            time.sleep(self.action_delay)
        return count

    def greet_one_new_follower(self, reply_provider: Callable[[str], str]) -> bool:
        if not self._open_new_followers():
            return False
        buttons = self.driver.find_elements(By.XPATH, '//*[@text="Follow back"]')
        if not buttons:
            return False
        button = buttons[0]
        rect = button.rect
        row_y = int(rect["y"] + rect["height"] / 2)
        button.click()
        time.sleep(self.action_delay)
        self.driver.execute_script("mobile: clickGesture", {"x": 270, "y": row_y})
        time.sleep(self.action_delay)
        messages = self.driver.find_elements(By.XPATH, '//*[@text="Message"]')
        if not messages:
            return False
        messages[0].click()
        time.sleep(self.action_delay)
        editor = self.driver.find_element(By.CLASS_NAME, "android.widget.EditText")
        reply = reply_provider(
            "A new follower connected with me. Write a friendly first TikTok DM."
        )
        editor.send_keys(reply)
        time.sleep(self.action_delay)
        send = self.driver.find_elements(
            By.XPATH, '//*[@text="Send" or @content-desc="Send"]'
        )
        if not send:
            return False
        send[0].click()
        time.sleep(self.action_delay)
        return True

    def reply_to_inbox_event(
        self, title: str, message: str, reply_provider: Callable[[str], str]
    ) -> bool:
        self.driver.execute_script(
            "mobile: deepLink",
            {"url": "tiktok://inbox", "package": TIKTOK_PACKAGE},
        )
        time.sleep(self.action_delay)
        safe_title = title.replace('"', "")
        threads = self.driver.find_elements(
            By.XPATH, f'//*[@text="{safe_title}" or @content-desc="{safe_title}"]'
        )
        if not threads:
            return False
        threads[0].click()
        time.sleep(self.action_delay)
        editor = self.driver.find_element(By.CLASS_NAME, "android.widget.EditText")
        reply = reply_provider(message)
        editor.send_keys(reply)
        time.sleep(self.action_delay)
        send = self.driver.find_elements(
            By.XPATH, '//*[@text="Send" or @content-desc="Send"]'
        )
        if not send:
            return False
        send[0].click()
        time.sleep(self.action_delay)
        return True

    def _open_new_followers(self) -> bool:
        self.driver.execute_script(
            "mobile: deepLink",
            {"url": "tiktok://inbox", "package": TIKTOK_PACKAGE},
        )
        time.sleep(self.action_delay)
        activity = self.driver.find_elements(
            By.XPATH, '//*[@text="Activity & new followers"]'
        )
        if not activity:
            return False
        activity[0].click()
        time.sleep(self.action_delay)
        new_followers = self.driver.find_elements(
            By.XPATH, '//*[@text="New followers"]'
        )
        if new_followers:
            new_followers[0].click()
            time.sleep(self.action_delay)
        return True

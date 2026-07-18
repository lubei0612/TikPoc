import time
from collections.abc import Callable
from typing import Protocol

from selenium.webdriver.common.by import By

from .acquisition_models import (
    ActionResult,
    AssignmentPhase,
    DeviceDiagnostics,
    OutcomeKind,
    PoolTarget,
    ProfileAccessState,
    ProfileObservation,
)
from .models import ProfileMetrics
from .profile_parser import parse_profile_page, parse_visible_post_keys


TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
POST_CONTAINER_ID = f"{TIKTOK_PACKAGE}:id/eqx"
PROFILE_USERNAME_ID = f"{TIKTOK_PACKAGE}:id/s7e"


class ProfileIdentityMismatch(ValueError):
    pass


LIKE_CONTROL_XPATH = '//*[starts-with(@content-desc, "Like video.")]'
LIKE_ACTIVE_XPATH = (
    '//*[@content-desc="Video liked" or starts-with(@content-desc,"Unlike video")]'
)
FAVORITE_CONTROL_XPATH = (
    '//*[@content-desc="Add or remove this video from Favorites."]/..'
)
FAVORITE_ACTIVE_XPATH = (
    '//*[contains(@content-desc,"Remove from Favorites") or '
    'contains(@text,"Added to Favorites") or contains(@content-desc,"Added to Favorites")]'
)
SHARE_CONTROL_XPATH = '//*[starts-with(@content-desc, "Share video.")]'
REPOST_CONTROL_XPATH = '//*[@text="Repost" or @content-desc="Repost"]'
REPOST_ACTIVE_XPATH = (
    '//*[contains(@text,"You reposted") or contains(@content-desc,"You reposted") or '
    'contains(@text,"Remove repost") or contains(@content-desc,"Remove repost")]'
)


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
        action_timeout: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.driver = driver
        self.metric_read_attempts = metric_read_attempts
        self.poll_interval = poll_interval
        self.action_delay = action_delay
        self.action_timeout = max(0.0, action_timeout)
        self.clock = clock
        self.sleeper = sleeper

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
                    last_error = ProfileIdentityMismatch(
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
        try:
            outcome = OutcomeKind.REPOST if action == "share" else OutcomeKind(action)
        except KeyError as error:
            raise ValueError(f"unsupported action: {action}") from error
        except ValueError as error:
            raise ValueError(f"unsupported action: {action}") from error
        result = self.execute_outcome(outcome)
        if result is not ActionResult.CONFIRMED:
            raise RuntimeError(f"{action} action was not verified")
        return True

    def execute_outcome(self, outcome: OutcomeKind) -> ActionResult:
        normalized = OutcomeKind(outcome)
        if normalized is OutcomeKind.TRACE:
            return ActionResult.CONFIRMED
        if self._outcome_state(normalized) is True:
            return ActionResult.CONFIRMED

        if normalized is OutcomeKind.REPOST:
            repost = self._first_visible(REPOST_CONTROL_XPATH)
            if repost is None:
                share = self._first_visible(SHARE_CONTROL_XPATH)
                if share is None:
                    return ActionResult.UNCERTAIN
                share.click()
                repost = self._wait_for_element(REPOST_CONTROL_XPATH)
                if repost is None:
                    return ActionResult.UNCERTAIN
            repost.click()
        else:
            selector = (
                LIKE_CONTROL_XPATH
                if normalized is OutcomeKind.LIKE
                else FAVORITE_CONTROL_XPATH
            )
            control = self._first_visible(selector)
            if control is None:
                return ActionResult.UNCERTAIN
            control.click()
        return (
            ActionResult.CONFIRMED
            if self._wait_for_active(normalized)
            else ActionResult.UNCERTAIN
        )

    def reconcile_outcome(self, outcome: OutcomeKind) -> ActionResult:
        state = self._outcome_state(OutcomeKind(outcome))
        if state is True:
            return ActionResult.CONFIRMED
        if state is False:
            return ActionResult.NOT_APPLIED
        return ActionResult.UNCERTAIN

    def open_target(self, target: PoolTarget) -> None:
        self.open_profile(target.username)

    def confirm_profile_identity(self, target: PoolTarget) -> None:
        self.wait_profile_ready(target.username)

    def read_profile_observation(self) -> ProfileObservation:
        page_source = str(self.driver.page_source)
        lowered = page_source.lower()
        if "this account is private" in lowered or "此帐户为私密帐户" in page_source:
            return ProfileObservation(
                observed_username=self._visible_profile_username(),
                metrics=None,
                private_account=True,
                access_state=ProfileAccessState.PRIVATE,
            )
        return ProfileObservation(
            observed_username=self._visible_profile_username(),
            metrics=self.read_profile_metrics(),
            private_account=False,
            access_state=ProfileAccessState.PUBLIC,
        )

    def list_video_keys(self) -> tuple[str, ...]:
        return self.list_visible_posts()

    def open_and_confirm_video(self, video_key: str) -> None:
        self.open_post(video_key)
        if self._wait_for_element(SHARE_CONTROL_XPATH) is None:
            raise RuntimeError("video controls did not become visible")

    def capture_diagnostics(self) -> DeviceDiagnostics:
        return DeviceDiagnostics(ui_summary=str(self.driver.page_source)[:2_000])

    def recover(self, phase: AssignmentPhase) -> None:
        self.sleeper(self.poll_interval)

    def _visible_profile_username(self) -> str:
        elements = self.driver.find_elements(By.ID, PROFILE_USERNAME_ID)
        if elements:
            return str(elements[0].text or "").strip().removeprefix("@").lower()
        try:
            return parse_profile_page(self.driver.page_source).username
        except ValueError:
            return ""

    def _outcome_state(self, outcome: OutcomeKind) -> bool | None:
        if outcome is OutcomeKind.TRACE:
            return True
        if outcome is OutcomeKind.LIKE:
            if self._first_visible(LIKE_ACTIVE_XPATH) is not None:
                return True
            if self._first_visible(LIKE_CONTROL_XPATH) is not None:
                return False
            return None
        if outcome is OutcomeKind.FAVORITE:
            if self._first_visible(FAVORITE_ACTIVE_XPATH) is not None:
                return True
            control = self._first_visible(FAVORITE_CONTROL_XPATH)
            if control is None:
                return None
            for attribute in ("selected", "checked"):
                value = str(control.get_attribute(attribute) or "").strip().lower()
                if value in {"true", "1"}:
                    return True
                if value in {"false", "0"}:
                    return False
            description = str(control.get_attribute("content-desc") or "").lower()
            if "remove from favorites" in description:
                return True
            if "add to favorites" in description:
                return False
            return None
        if self._first_visible(REPOST_ACTIVE_XPATH) is not None:
            return True
        if self._first_visible(REPOST_CONTROL_XPATH) is not None:
            return False
        if self._first_visible(SHARE_CONTROL_XPATH) is not None:
            return False
        return None

    def _first_visible(self, selector: str):
        try:
            elements = self.driver.find_elements(By.XPATH, selector)
        except Exception:
            return None
        return elements[0] if elements else None

    def _wait_for_element(self, selector: str):
        deadline = self.clock() + self.action_timeout
        while True:
            element = self._first_visible(selector)
            if element is not None:
                return element
            if self.clock() >= deadline:
                return None
            self.sleeper(self.poll_interval)

    def _wait_for_active(self, outcome: OutcomeKind) -> bool:
        deadline = self.clock() + self.action_timeout
        while True:
            if self._outcome_state(outcome) is True:
                return True
            if self.clock() >= deadline:
                return False
            self.sleeper(self.poll_interval)

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

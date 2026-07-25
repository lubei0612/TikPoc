import hashlib
import os
import time
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Protocol

from appium.webdriver.common.appiumby import AppiumBy
from PIL import Image, UnidentifiedImageError
from selenium.common.exceptions import WebDriverException
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
from .device_performance import DevicePerformanceSnapshot
from .models import ProfileMetrics
from .profile_parser import (
    parse_profile_page,
    parse_profile_username,
    parse_visible_post_keys,
    private_profile_visible,
    profile_recommendations_visible,
    profile_surface_visible,
)

TIKTOK_PACKAGE = "com.zhiliaoapp.musically"
POST_CONTAINER_ID = f"{TIKTOK_PACKAGE}:id/eqx"
POST_CONTAINER_IDS = (
    POST_CONTAINER_ID,
    f"{TIKTOK_PACKAGE}:id/efq",
    f"{TIKTOK_PACKAGE}:id/cover",
    f"{TIKTOK_PACKAGE}:id/dp6",
)
POST_CONTAINER_XPATH = (
    "//*["
    + " or ".join(f'@resource-id="{resource_id}"' for resource_id in POST_CONTAINER_IDS)
    + "]"
)
PROFILE_USERNAME_ID = f"{TIKTOK_PACKAGE}:id/s7e"
PROFILE_USERNAME_IDS = (
    PROFILE_USERNAME_ID,
    f"{TIKTOK_PACKAGE}:id/rgn",
    f"{TIKTOK_PACKAGE}:id/oul",
)
PROFILE_STAT_LABEL_IDS = (
    f"{TIKTOK_PACKAGE}:id/s5x",
    f"{TIKTOK_PACKAGE}:id/rfc",
    f"{TIKTOK_PACKAGE}:id/oth",
    f"{TIKTOK_PACKAGE}:id/ops",
)
PROFILE_VIDEO_TAB_XPATH = (
    '//*[@clickable="true" and (@content-desc="视频" or @content-desc="Videos")]'
)


class ProfileIdentityMismatch(ValueError):
    pass


LIKE_CONTROL_XPATH = (
    '//*[starts-with(@content-desc, "Like video.") or '
    'starts-with(@content-desc, "点赞视频。")]'
)
LIKE_ACTIVE_XPATH = (
    '//*[@content-desc="Video liked" or starts-with(@content-desc,"Unlike video") or '
    'starts-with(@content-desc,"取消点赞视频") or contains(@content-desc,"已点赞") or '
    'contains(@content-desc,"点赞的视频")]'
)
FAVORITE_CONTROL_XPATH = (
    '//*[@content-desc="Add or remove this video from Favorites." or '
    'starts-with(@content-desc,"将此视频添加到或移出收藏")]'
)
FAVORITE_ACTIVE_XPATH = (
    '//*[contains(@content-desc,"Remove from Favorites") or '
    'contains(@text,"Added to Favorites") or contains(@content-desc,"Added to Favorites") or '
    'contains(@text,"从收藏中移除") or contains(@content-desc,"从收藏中移除") or '
    'contains(@text,"已添加到收藏") or contains(@content-desc,"已添加到收藏")]'
)
SHARE_CONTROL_XPATH = (
    '//*[starts-with(@content-desc, "Share video.") or '
    'starts-with(@content-desc, "分享视频。")]'
)
SHARE_CONTROL_UIAUTOMATOR_SELECTORS = (
    'new UiSelector().descriptionStartsWith("分享视频。")',
    'new UiSelector().descriptionStartsWith("Share video.")',
)
REPOST_CONTROL_XPATH = (
    '//*[@text="Repost" or @content-desc="Repost" or '
    '@text="转发" or @content-desc="转发"]'
)
SHARE_SURFACE_XPATH = '//*[@content-desc="Bottom sheet" or @content-desc="底部工作表"]'
COPY_LINK_XPATH = (
    '//*[@text="Copy link" or @content-desc="Copy link" or '
    '@text="复制链接" or @content-desc="复制链接"]'
)
REPOST_ACTIVE_XPATH = (
    '//*[contains(@text,"You reposted") or contains(@content-desc,"You reposted") or '
    'contains(@text,"Remove repost") or contains(@content-desc,"Remove repost") or '
    'contains(@text,"已转发") or contains(@content-desc,"已转发") or '
    'contains(@text,"取消转发") or contains(@content-desc,"取消转发")]'
)
LIKE_STATE_XPATH = f"{LIKE_ACTIVE_XPATH} | {LIKE_CONTROL_XPATH}"
FAVORITE_STATE_XPATH = f"{FAVORITE_ACTIVE_XPATH} | {FAVORITE_CONTROL_XPATH}"
REPOST_STATE_XPATH = (
    f"{REPOST_ACTIVE_XPATH} | {REPOST_CONTROL_XPATH} | {SHARE_CONTROL_XPATH}"
)


def _favorite_pixel_state(png_bytes: bytes) -> bool | None:
    try:
        with Image.open(BytesIO(png_bytes)) as image:
            colors = image.convert("RGB").getcolors(
                maxcolors=image.width * image.height
            )
            return any(
                red > 200 and green > 140 and blue < 100
                for _, (red, green, blue) in (colors or ())
            )
    except (OSError, UnidentifiedImageError):
        return None


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
        action_timeout: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        route_opener: Callable[[str], None] | None = None,
        diagnostics_dir: Path | None = None,
    ) -> None:
        self.driver = driver
        self.metric_read_attempts = metric_read_attempts
        self.poll_interval = poll_interval
        self.action_delay = action_delay
        self.action_timeout = max(0.0, action_timeout)
        self.clock = clock
        self.sleeper = sleeper
        self.route_opener = route_opener
        self.diagnostics_dir = diagnostics_dir
        self._profile_source: str | None = None
        self._confirmed_profile_username = ""
        self._visible_post_elements: tuple[object, ...] | None = None

    def performance_snapshot(self) -> DevicePerformanceSnapshot:
        snapshotter = getattr(self.driver, "performance_snapshot", None)
        if snapshotter is None:
            return DevicePerformanceSnapshot()
        snapshot = snapshotter()
        return (
            snapshot
            if isinstance(snapshot, DevicePerformanceSnapshot)
            else DevicePerformanceSnapshot()
        )

    def bind_assignment(
        self,
        assignment_id: int,
        phase: AssignmentPhase,
        *,
        account_id: str,
        fence_token: int,
    ) -> None:
        return

    def _invalidate_profile_source(self) -> None:
        self._profile_source = None
        self._visible_post_elements = None

    def _swipe_profile_grid(self) -> None:
        try:
            window = self.driver.get_window_size()
            width = max(1, int(window["width"]))
            height = max(1, int(window["height"]))
        except (AttributeError, KeyError, TypeError, ValueError, WebDriverException):
            self.driver.swipe(540, 1900, 540, 1050, 600)
            return
        center_x = width // 2
        self.driver.swipe(
            center_x,
            int(height * 0.9),
            center_x,
            int(height * 0.5),
            600,
        )

    def _open_route(self, uri: str) -> None:
        self._invalidate_profile_source()
        if self.route_opener is not None:
            self.route_opener(uri)
            return
        self.driver.execute_script(
            "mobile: deepLink", {"url": uri, "package": TIKTOK_PACKAGE}
        )

    def _restart_route(self, uri: str) -> None:
        self._invalidate_profile_source()
        self.driver.terminate_app(TIKTOK_PACKAGE)
        self._open_route(uri)

    def ensure_ready(self) -> None:
        self.driver.activate_app(TIKTOK_PACKAGE)

    def open_profile(self, username: str) -> None:
        self._invalidate_profile_source()
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
            elements = self._profile_username_elements()
            actual = ""
            if elements:
                try:
                    actual = (
                        str(elements[0].text or "").strip().removeprefix("@").lower()
                    )
                except Exception:
                    try:
                        actual = parse_profile_page(self.driver.page_source).username
                    except Exception:
                        pass
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
                self._swipe_profile_grid()
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
        elements = self._visible_post_elements
        if elements is None:
            for attempt in range(self.metric_read_attempts):
                elements = tuple(self._post_elements())
                if elements or attempt + 1 == self.metric_read_attempts:
                    break
                self.sleeper(self.poll_interval)
        self._visible_post_elements = elements
        return tuple(str(index) for index in range(len(elements)))

    def open_post(self, post_id: str) -> None:
        elements = self._visible_post_elements
        self._visible_post_elements = None
        if elements is None:
            elements = tuple(self._post_elements())
        index = int(post_id)
        if index < 0 or index >= len(elements):
            raise ValueError(f"post is no longer visible: {post_id}")
        self._invalidate_profile_source()
        elements[index].click()

    def _post_elements(self):
        elements = self.driver.find_elements(By.XPATH, POST_CONTAINER_XPATH)
        visible_elements = []
        for element in elements:
            try:
                if element.is_displayed():
                    visible_elements.append(element)
            except Exception:
                continue
        return visible_elements

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
        if normalized in {OutcomeKind.LIKE, OutcomeKind.FAVORITE}:
            state, control, control_kind = self._wait_for_outcome_observation(
                normalized
            )
        else:
            state, control, control_kind = self._outcome_observation(normalized)
        if state is True:
            return ActionResult.CONFIRMED

        if normalized is OutcomeKind.REPOST:
            if control_kind != "repost":
                if control is None or control_kind != "share":
                    return ActionResult.UNCERTAIN
                control.click()
                repost = self._wait_for_outcome_control(normalized, "repost")
                if repost is None:
                    if self._share_surface_visible():
                        return ActionResult.UNAVAILABLE
                    return ActionResult.UNCERTAIN
            else:
                repost = control
            if repost is None:
                return ActionResult.UNCERTAIN
            repost.click()
        else:
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
        if target.target_id:
            self._profile_before_stable_route = (
                self._confirmed_profile_username or self._visible_profile_username()
            )
            self._stable_profile_uri = f"snssdk1233://user/profile/{target.target_id}"
            self._open_route(self._stable_profile_uri)
            return
        self._stable_profile_uri = ""
        self.open_profile(target.username)

    def confirm_profile_identity(self, target: PoolTarget) -> None:
        stable_uri = getattr(self, "_stable_profile_uri", "")
        if stable_uri:
            expected = target.username.strip().removeprefix("@").lower()
            previous = getattr(self, "_profile_before_stable_route", "")
            identity_seen = False
            for attempt in range(self.metric_read_attempts):
                marker_username = self._semantic_profile_username()
                marker_matches = bool(
                    marker_username
                    and (marker_username == expected or marker_username != previous)
                )
                source = ""
                source_username = ""
                ready = False
                if marker_matches or attempt % 3 == 2:
                    try:
                        source = str(self.driver.page_source)
                        source_username = parse_profile_username(source)
                        ready = profile_surface_visible(source)
                    except Exception:
                        source = ""
                        source_username = ""
                        ready = False
                actual = source_username or marker_username
                source_matches_marker = not (
                    source_username
                    and marker_username
                    and source_username != marker_username
                )
                identity_seen = (
                    identity_seen
                    or marker_matches
                    or bool(actual and (actual == expected or actual != previous))
                )
                if (
                    actual
                    and ready
                    and source_matches_marker
                    and (actual == expected or actual != previous)
                ):
                    self._profile_source = source
                    self._confirmed_profile_username = actual
                    return
                if attempt and attempt % 3 == 0:
                    self._open_route(stable_uri)
                if attempt + 1 < self.metric_read_attempts:
                    self.sleeper(self.poll_interval)
            if identity_seen:
                raise ValueError("profile surface did not become ready")
            self._restart_route(stable_uri)
            if self._wait_any_profile_surface(reject_username=previous):
                return
            username_uri = target.profile_url.strip() or (
                f"https://www.tiktok.com/@{expected}"
            )
            self._open_route(username_uri)
            try:
                self.wait_profile_ready(expected)
                self._wait_profile_surface(expected)
                self._confirmed_profile_username = expected
                return
            except ProfileIdentityMismatch:
                raise
            except ValueError as error:
                raise ValueError(
                    "stable and username profile routes did not load"
                ) from error
        self.wait_profile_ready(target.username)
        expected = target.username.strip().removeprefix("@").lower()
        self._wait_profile_surface(expected)
        self._confirmed_profile_username = expected

    def read_profile_observation(self) -> ProfileObservation:
        source = self._profile_source
        last_error: ValueError | None = None
        video_tab_attempted = False
        for attempt in range(self.metric_read_attempts):
            if source is None:
                source = str(self.driver.page_source)
                self._profile_source = source
            observed_username = (
                parse_profile_username(source) or self._confirmed_profile_username
            )
            if (
                self._confirmed_profile_username
                and observed_username
                and observed_username != self._confirmed_profile_username
            ):
                last_error = ValueError("profile source identity is stale")
                source = None
                if attempt + 1 < self.metric_read_attempts:
                    self.sleeper(self.poll_interval)
                continue
            lowered = source.lower()
            if "account banned" in lowered or "no longer available" in lowered:
                return ProfileObservation(
                    observed_username=observed_username,
                    metrics=None,
                    private_account=False,
                    access_state=ProfileAccessState.SUSPENDED,
                )
            if any(
                marker in lowered
                for marker in (
                    "couldn't find this account",
                    "account not found",
                    "user not found",
                )
            ):
                return ProfileObservation(
                    observed_username=observed_username,
                    metrics=None,
                    private_account=False,
                    access_state=ProfileAccessState.MISSING,
                )
            if private_profile_visible(source):
                return ProfileObservation(
                    observed_username=observed_username,
                    metrics=None,
                    private_account=True,
                    access_state=ProfileAccessState.PRIVATE,
                )
            try:
                page = parse_profile_page(source)
            except ValueError as error:
                last_error = error
                source = None
                if attempt + 1 < self.metric_read_attempts:
                    self.sleeper(self.poll_interval)
                continue
            metrics = page.metrics
            if metrics.posts == 0 and metrics.following > metrics.followers:
                semantic_posts = tuple(self._post_elements())
                if semantic_posts:
                    self._visible_post_elements = semantic_posts
                    metrics = ProfileMetrics(
                        following=metrics.following,
                        followers=metrics.followers,
                        posts=len(semantic_posts),
                    )
                else:
                    if profile_recommendations_visible(source):
                        self._swipe_profile_grid()
                        self.sleeper(self.poll_interval)
                        settled_source = str(self.driver.page_source)
                        self._profile_source = settled_source
                        if private_profile_visible(settled_source):
                            return ProfileObservation(
                                observed_username=observed_username,
                                metrics=None,
                                private_account=True,
                                access_state=ProfileAccessState.PRIVATE,
                            )
                        settled_posts = tuple(self._post_elements())
                        if settled_posts:
                            self._visible_post_elements = settled_posts
                            metrics = ProfileMetrics(
                                following=metrics.following,
                                followers=metrics.followers,
                                posts=len(settled_posts),
                            )
                            return ProfileObservation(
                                observed_username=observed_username,
                                metrics=metrics,
                                private_account=False,
                                access_state=ProfileAccessState.PUBLIC,
                            )
                        else:
                            return ProfileObservation(
                                observed_username=observed_username,
                                metrics=metrics,
                                private_account=False,
                                access_state=ProfileAccessState.PUBLIC,
                            )
                    if not video_tab_attempted:
                        video_tab = self._first_visible(PROFILE_VIDEO_TAB_XPATH)
                        video_tab_attempted = True
                        if video_tab is not None:
                            video_tab.click()
                            last_error = ValueError("profile video tab is loading")
                            source = None
                            self._profile_source = None
                            if attempt + 1 < self.metric_read_attempts:
                                self.sleeper(self.poll_interval)
                                continue
                    if attempt + 1 < self.metric_read_attempts:
                        last_error = ValueError("profile post grid is not ready")
                        source = None
                        self._profile_source = None
                        self.sleeper(self.poll_interval)
                        continue
            if page.visible_post_count == 3:
                self._swipe_profile_grid()
                self.sleeper(self.poll_interval)
                next_source = str(self.driver.page_source)
                self._profile_source = next_source
                next_keys = parse_visible_post_keys(next_source)
                metrics = ProfileMetrics(
                    following=metrics.following,
                    followers=metrics.followers,
                    posts=(4 if set(next_keys) - set(page.visible_post_keys) else 3),
                )
            return ProfileObservation(
                observed_username=page.username,
                metrics=metrics,
                private_account=False,
                access_state=ProfileAccessState.PUBLIC,
            )
        if self._confirmed_profile_username:
            return ProfileObservation(
                observed_username=self._confirmed_profile_username,
                metrics=None,
                private_account=False,
                access_state=ProfileAccessState.INACCESSIBLE,
            )
        raise last_error or ValueError("profile metrics are incomplete")

    def list_video_keys(self) -> tuple[str, ...]:
        return self.list_visible_posts()

    def open_and_confirm_video(self, video_key: str) -> None:
        self.open_post(video_key)
        if self._wait_for_video_control() is None:
            raise RuntimeError("video controls did not become visible")

    def capture_diagnostics(self) -> DeviceDiagnostics:
        try:
            source = str(self.driver.page_source)
        except Exception:
            source = ""
        try:
            activity = str(getattr(self.driver, "current_activity", "unknown"))
        except Exception:
            activity = "unavailable"
        try:
            window = self.driver.get_window_size()
            window_summary = f"{int(window['width'])}x{int(window['height'])}"
        except Exception:
            window_summary = "unavailable"

        controls: list[str] = []
        for label, xpath in (
            ("like", LIKE_STATE_XPATH),
            ("favorite", FAVORITE_STATE_XPATH),
            ("share", SHARE_CONTROL_XPATH),
            ("repost", REPOST_STATE_XPATH),
        ):
            try:
                elements = self.driver.find_elements(By.XPATH, xpath)
            except Exception:
                continue
            for element in elements[:2]:
                try:
                    if not element.is_displayed():
                        continue
                    rect = element.rect
                    descriptor = str(
                        element.get_attribute("content-desc")
                        or element.get_attribute("text")
                        or getattr(element, "text", "")
                        or label
                    ).strip()
                    controls.append(
                        f"{label}:{descriptor}@{int(rect['x'])},{int(rect['y'])},"
                        f"{int(rect['width'])}x{int(rect['height'])}"
                    )
                except Exception:
                    continue
        screenshot_path = ""
        if self.diagnostics_dir is not None:
            try:
                self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
                candidate = self.diagnostics_dir / (
                    f"diagnostic-{os.getpid()}-{time.time_ns()}.png"
                )
                saved = self.driver.save_screenshot(str(candidate))
                if saved and candidate.is_file():
                    screenshot_path = str(candidate)
                elif candidate.exists():
                    candidate.unlink()
            except Exception:
                screenshot_path = ""
        summary = "; ".join(
            (
                f"activity={activity}",
                f"window={window_summary}",
                f"source_sha256={hashlib.sha256(source.encode()).hexdigest()[:16]}",
                f"source_length={len(source)}",
                f"controls={'|'.join(controls) or 'none'}",
            )
        )
        return DeviceDiagnostics(screenshot_path=screenshot_path, ui_summary=summary)

    def recover(self, phase: AssignmentPhase) -> None:
        self.sleeper(self.poll_interval)

    def _visible_profile_username(self) -> str:
        username = self._semantic_profile_username()
        if username:
            return username
        try:
            return parse_profile_page(self.driver.page_source).username
        except Exception:
            return ""

    def _semantic_profile_username(self) -> str:
        elements = self._profile_username_elements()
        if not elements:
            return ""
        try:
            return str(elements[0].text or "").strip().removeprefix("@").lower()
        except Exception:
            return ""

    def _profile_username_elements(self):
        for resource_id in PROFILE_USERNAME_IDS:
            try:
                elements = self.driver.find_elements(By.ID, resource_id)
            except Exception:
                continue
            if elements:
                return elements
        return []

    def _wait_any_profile_surface(self, *, reject_username: str = "") -> bool:
        for attempt in range(self.metric_read_attempts):
            try:
                source = str(self.driver.page_source)
                actual = parse_profile_username(source)
                ready = profile_surface_visible(source)
            except Exception:
                source = ""
                actual = ""
                ready = False
            if actual and ready and actual != reject_username:
                self._profile_source = source
                self._confirmed_profile_username = actual
                return True
            if attempt + 1 < self.metric_read_attempts:
                self.sleeper(self.poll_interval)
        return False

    def _wait_profile_surface(self, confirmed_username: str = "") -> None:
        for attempt in range(self.metric_read_attempts):
            page_source = str(self.driver.page_source)
            source_username = parse_profile_username(page_source)
            cache_source = not (
                confirmed_username
                and source_username
                and source_username != confirmed_username
            )
            lowered = page_source.lower()
            if (
                "this account is private" in lowered
                or "此帐户为私密帐户" in page_source
            ):
                self._profile_source = page_source if cache_source else None
                return
            try:
                parse_profile_page(page_source)
                self._profile_source = page_source if cache_source else None
                return
            except ValueError:
                pass
            try:
                if profile_surface_visible(page_source):
                    self._profile_source = page_source if cache_source else None
                    return
            except ValueError:
                pass
            if attempt + 1 < self.metric_read_attempts:
                self.sleeper(self.poll_interval)
        raise ValueError("profile surface did not become ready")

    def _outcome_state(self, outcome: OutcomeKind) -> bool | None:
        state, _, _ = self._outcome_observation(outcome)
        return state

    def _outcome_observation(
        self, outcome: OutcomeKind
    ) -> tuple[bool | None, object | None, str]:
        if outcome is OutcomeKind.TRACE:
            return True, None, ""
        if outcome is OutcomeKind.LIKE:
            elements = self._visible_elements(LIKE_STATE_XPATH)
            for element in elements:
                semantics = self._element_semantics(element)
                if (
                    "video liked" in semantics
                    or "unlike video" in semantics
                    or "取消点赞视频" in semantics
                    or "已点赞" in semantics
                    or "点赞的视频" in semantics
                ):
                    return True, None, ""
            return (
                (False, elements[0], "like") if len(elements) == 1 else (None, None, "")
            )
        if outcome is OutcomeKind.FAVORITE:
            elements = self._visible_elements(FAVORITE_STATE_XPATH)
            for element in elements:
                semantics = self._element_semantics(element)
                if (
                    "remove from favorites" in semantics
                    or "added to favorites" in semantics
                    or "从收藏中移除" in semantics
                    or "已添加到收藏" in semantics
                ):
                    return True, None, ""
            if len(elements) != 1:
                return None, None, ""
            control = elements[0]
            pixel_state = _favorite_pixel_state(control.screenshot_as_png)
            if pixel_state is True:
                return True, None, ""
            for attribute in ("selected", "checked"):
                value = str(control.get_attribute(attribute) or "").strip().lower()
                if value in {"true", "1"}:
                    return True, None, ""
                if value in {"false", "0"}:
                    return False, control, "favorite"
            return pixel_state, control, "favorite"
        elements = self._visible_elements(REPOST_STATE_XPATH)
        for element in elements:
            semantics = self._element_semantics(element)
            if (
                "you reposted" in semantics
                or "remove repost" in semantics
                or "已转发" in semantics
                or "取消转发" in semantics
            ):
                return True, None, ""
        repost_controls = [
            element
            for element in elements
            if (semantics := self._element_semantics(element))
            and all(value in {"repost", "转发"} for value in semantics.split())
        ]
        if len(repost_controls) == 1:
            return False, repost_controls[0], "repost"
        if repost_controls:
            return None, None, ""
        share_controls = [
            element
            for element in elements
            if "share video" in (semantics := self._element_semantics(element))
            or semantics == "share"
            or "分享视频" in semantics
            or semantics == "分享"
        ]
        if len(share_controls) == 1:
            return False, share_controls[0], "share"
        return None, None, ""

    def _visible_elements(self, selector: str) -> list[object]:
        try:
            elements = self.driver.find_elements(By.XPATH, selector)
        except Exception:
            return []
        visible = []
        for element in elements:
            try:
                if element.is_displayed():
                    visible.append(element)
            except Exception:
                continue
        return visible

    @staticmethod
    def _element_semantics(element: object) -> str:
        values = []
        for attribute in ("content-desc", "text"):
            try:
                values.append(str(element.get_attribute(attribute) or ""))
            except Exception:
                pass
        try:
            values.append(str(element.text or ""))
        except Exception:
            pass
        return " ".join(values).strip().lower()

    def _first_visible(self, selector: str):
        return self._first_visible_by(By.XPATH, selector)

    def _first_visible_by(self, by: str, selector: str):
        try:
            elements = self.driver.find_elements(by, selector)
        except Exception:
            return None
        for element in elements:
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
        return None

    def _wait_for_video_control(self):
        deadline = self.clock() + self.action_timeout
        while True:
            for selector in SHARE_CONTROL_UIAUTOMATOR_SELECTORS:
                element = self._first_visible_by(AppiumBy.ANDROID_UIAUTOMATOR, selector)
                if element is not None:
                    return element
            if self.clock() >= deadline:
                return self._first_visible(SHARE_CONTROL_XPATH)
            self.sleeper(self.poll_interval)

    def _share_surface_visible(self) -> bool:
        return (
            self._first_visible(SHARE_SURFACE_XPATH) is not None
            and self._first_visible(COPY_LINK_XPATH) is not None
        )

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

    def _wait_for_outcome_observation(
        self, outcome: OutcomeKind
    ) -> tuple[bool | None, object | None, str]:
        deadline = self.clock() + self.action_timeout
        previous_kind = ""
        while True:
            observation = self._outcome_observation(outcome)
            state, control, control_kind = observation
            if state is True:
                return observation
            if control is not None and control_kind and control_kind == previous_kind:
                return observation
            previous_kind = control_kind if control is not None else ""
            if self.clock() >= deadline:
                return observation
            self.sleeper(self.poll_interval)

    def _wait_for_outcome_control(self, outcome: OutcomeKind, kind: str):
        deadline = self.clock() + self.action_timeout
        while True:
            _, control, control_kind = self._outcome_observation(outcome)
            if control is not None and control_kind == kind:
                return control
            if self.clock() >= deadline:
                return None
            self.sleeper(self.poll_interval)

    def return_to_baseline(self) -> None:
        self._invalidate_profile_source()
        self.driver.back()

    def restart_app(self) -> None:
        self._invalidate_profile_source()
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

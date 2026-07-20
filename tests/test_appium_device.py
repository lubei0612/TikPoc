from tests.test_profile_parser import PROFILE_XML
import pytest
from selenium.common.exceptions import StaleElementReferenceException
from tikpoc.acquisition_models import (
    ActionResult,
    OutcomeKind,
    PoolTarget,
    ProfileAccessState,
)
from io import BytesIO

from PIL import Image
from tikpoc.device import (
    AppiumTikTokDevice,
    ProfileIdentityMismatch,
    ProfilePermanentlyUnavailable,
    _favorite_pixel_state,
    _terminal_profile_marker,
)
from tikpoc.models import ProfileMetrics
from tikpoc.profile_parser import parse_profile_username


class FakeElement:
    def __init__(self, text: str = "", *, on_click=None, attributes=None) -> None:
        self.clicked = False
        self.value = ""
        self.text = text
        self.on_click = on_click
        self.attributes = attributes or {}
        self.rect = {"x": 800, "y": 700, "width": 200, "height": 80}

    def click(self) -> None:
        self.clicked = True
        if self.on_click is not None:
            self.on_click()

    def send_keys(self, value: str) -> None:
        self.value = value

    def get_attribute(self, name: str):
        value = self.attributes.get(name)
        return value() if callable(value) else value

    def is_displayed(self) -> bool:
        return bool(self.attributes.get("displayed", True))

    @property
    def screenshot_as_png(self) -> bytes:
        return b"after" if self.clicked else b"before"


class FakeDriver:
    def __init__(self) -> None:
        self.page_source = PROFILE_XML
        self.scripts: list[tuple[str, dict[str, str]]] = []
        self.posts = [FakeElement(), FakeElement(), FakeElement(), FakeElement()]
        self.liked = False
        self.favorite = False
        self.share_open = False
        self.reposted = False
        self.action_elements = {
            '//*[starts-with(@content-desc, "Like video.")]': FakeElement(
                "Like video.", on_click=lambda: setattr(self, "liked", True)
            ),
            '//*[@content-desc="Add or remove this video from Favorites."]': FakeElement(
                "Add or remove this video from Favorites.",
                on_click=lambda: setattr(self, "favorite", True),
                attributes={"selected": lambda: "true" if self.favorite else "false"},
            ),
            '//*[starts-with(@content-desc, "Share video.")]': FakeElement(
                "Share video.", on_click=lambda: setattr(self, "share_open", True)
            ),
            '//*[@text="Repost" or @content-desc="Repost"]': FakeElement(
                "Repost", on_click=lambda: setattr(self, "reposted", True)
            ),
        }
        self.back_calls = 0

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        self.scripts.append((name, arguments))

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if by == "id":
            if value in {
                "com.zhiliaoapp.musically:id/s7e",
                "com.zhiliaoapp.musically:id/rgn",
            }:
                return []
            assert value == "com.zhiliaoapp.musically:id/eqx"
            return self.posts
        if by == "xpath" and all(
            resource_id in value
            for resource_id in (
                "com.zhiliaoapp.musically:id/eqx",
                "com.zhiliaoapp.musically:id/efq",
            )
        ):
            return self.posts
        if "Video liked" in value and "Like video" in value:
            return (
                [FakeElement("Video liked")]
                if self.liked
                else [
                    self.action_elements[
                        '//*[starts-with(@content-desc, "Like video.")]'
                    ]
                ]
            )
        if "Remove from Favorites" in value and "Add or remove" in value:
            return (
                [FakeElement("Added to Favorites")]
                if self.favorite
                else [
                    self.action_elements[
                        '//*[@content-desc="Add or remove this video from Favorites."]'
                    ]
                ]
            )
        if "You reposted" in value and "Share video" in value:
            if self.reposted:
                return [FakeElement("You reposted")]
            if self.share_open:
                return [
                    self.action_elements[
                        '//*[@text="Repost" or @content-desc="Repost"]'
                    ]
                ]
            return [
                self.action_elements['//*[starts-with(@content-desc, "Share video.")]']
            ]
        if "Video liked" in value or "Unlike video" in value:
            return [FakeElement()] if self.liked else []
        if "Like video" in value:
            return [] if self.liked else [self.action_elements[value]]
        if "Remove from Favorites" in value or "Added to Favorites" in value:
            return [FakeElement()] if self.favorite else []
        if "Favorites" in value:
            return [
                self.action_elements[
                    '//*[@content-desc="Add or remove this video from Favorites."]'
                ]
            ]
        if "You reposted" in value or "Remove repost" in value:
            return [FakeElement()] if self.reposted else []
        if "Share video" in value:
            return [self.action_elements[value]]
        if "Repost" in value and self.share_open:
            return [
                self.action_elements['//*[@text="Repost" or @content-desc="Repost"]']
            ]
        return []

    def find_element(self, by: str, value: str) -> FakeElement:
        assert by == "xpath"
        return self.action_elements[value]

    def activate_app(self, package: str) -> None:
        assert package == "com.zhiliaoapp.musically"

    def terminate_app(self, package: str) -> None:
        assert package == "com.zhiliaoapp.musically"

    def back(self) -> None:
        self.back_calls += 1

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int
    ) -> None:
        return None


class DelayedProfileDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    @property
    def page_source(self) -> str:
        self.reads += 1
        return "<hierarchy />" if self.reads < 3 else PROFILE_XML

    @page_source.setter
    def page_source(self, value: str) -> None:
        return None


class ScrollingProfileDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        last_post = (
            '  <node resource-id="com.zhiliaoapp.musically:id/cover" />\n'
            '  <node text="404" resource-id="com.zhiliaoapp.musically:id/tv_play_count" />\n'
        )
        self.page_source = PROFILE_XML.replace(last_post, "")
        self.swipes = 0

    def swipe(
        self, start_x: int, start_y: int, end_x: int, end_y: int, duration: int
    ) -> None:
        self.swipes += 1
        self.page_source = self.page_source.replace('text="101"', 'text="404"')


class DelayedProfileMarkerDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.profile_marker_reads = 0

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            self.profile_marker_reads += 1
            if self.profile_marker_reads < 4:
                return []
            return [FakeElement("@sample")]
        return super().find_elements(by, value)


class WrongProfileMarkerDriver(FakeDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            return [FakeElement("@different_user")]
        return super().find_elements(by, value)


class CurrentProfileMarkerDriver(FakeDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/rgn":
            return [FakeElement("@sample")]
        if value == "com.zhiliaoapp.musically:id/s7e":
            return []
        return super().find_elements(by, value)


class StaleProfileMarker(FakeElement):
    @property
    def text(self) -> str:
        raise RuntimeError("stale marker")

    @text.setter
    def text(self, _value: str) -> None:
        return None


class StaleProfileMarkerDriver(FakeDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            return [StaleProfileMarker()]
        return super().find_elements(by, value)


class CachedProfileObservationDriver(FakeDriver):
    def __init__(self, page_source: str = PROFILE_XML) -> None:
        super().__init__()
        self._page_source = page_source
        self.page_source_reads = 0
        self.current_username = "previous"
        self.username_queries = 0

    @property
    def page_source(self) -> str:
        self.page_source_reads += 1
        return self._page_source

    @page_source.setter
    def page_source(self, value: str) -> None:
        self._page_source = value

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        if str(arguments.get("url") or "").startswith("snssdk1233://user/profile/"):
            self.current_username = "sample"

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if by == "id" and value in {
            "com.zhiliaoapp.musically:id/s7e",
            "com.zhiliaoapp.musically:id/rgn",
        }:
            self.username_queries += 1
            return [FakeElement(f"@{self.current_username}")]
        if by == "id" and value in {
            "com.zhiliaoapp.musically:id/s5x",
            "com.zhiliaoapp.musically:id/rfc",
        }:
            return [FakeElement("Following")]
        return super().find_elements(by, value)


class BoundedVideoDriver(CachedProfileObservationDriver):
    def __init__(self) -> None:
        bounded = PROFILE_XML.replace(
            'resource-id="com.zhiliaoapp.musically:id/cover"',
            'resource-id="com.zhiliaoapp.musically:id/cover" '
            'bounds="[100,200][300,600]"',
        )
        super().__init__(bounded)
        self.gestures: list[dict[str, int]] = []
        self.post_queries = 0
        self.posts = [FakeElement(on_click=self._open_video) for _ in range(4)]

    def _open_video(self) -> None:
        self._page_source = (
            '<hierarchy><node content-desc="Share video. 42 shares" '
            'bounds="[900,1000][1000,1100]" /></hierarchy>'
        )

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        if name == "mobile: clickGesture":
            self.gestures.append(arguments)
            self._page_source = (
                '<hierarchy><node content-desc="Share video. 42 shares" '
                'bounds="[900,1000][1000,1100]" /></hierarchy>'
            )
            return
        super().execute_script(name, arguments)

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if (
            by == "id"
            and value
            in {
                "com.zhiliaoapp.musically:id/eqx",
                "com.zhiliaoapp.musically:id/efq",
            }
        ) or (
            by == "xpath"
            and all(
                resource_id in value
                for resource_id in (
                    "com.zhiliaoapp.musically:id/eqx",
                    "com.zhiliaoapp.musically:id/efq",
                )
            )
        ):
            self.post_queries += 1
        if "Share video" in value:
            return [FakeElement()]
        return super().find_elements(by, value)


class SemanticOnlyVideoDriver(CachedProfileObservationDriver):
    def __init__(self, posts: list[FakeElement] | None = None) -> None:
        source = "\n".join(
            line
            for line in PROFILE_XML.splitlines()
            if "id/cover" not in line and "id/tv_play_count" not in line
        )
        super().__init__(source)
        if posts is not None:
            self.posts = posts
        self.post_queries = 0

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if (
            by == "id"
            and value
            in {
                "com.zhiliaoapp.musically:id/eqx",
                "com.zhiliaoapp.musically:id/efq",
            }
        ) or (
            by == "xpath"
            and all(
                resource_id in value
                for resource_id in (
                    "com.zhiliaoapp.musically:id/eqx",
                    "com.zhiliaoapp.musically:id/efq",
                )
            )
        ):
            self.post_queries += 1
            return self.posts
        return super().find_elements(by, value)


class CurrentSemanticOnlyVideoDriver(SemanticOnlyVideoDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if by == "id" and value == "com.zhiliaoapp.musically:id/eqx":
            self.post_queries += 1
            return []
        return super().find_elements(by, value)


class DisplayCheckFailureElement(FakeElement):
    def is_displayed(self) -> bool:
        raise RuntimeError("element became stale during visibility check")


class MissingVideoControlDriver(BoundedVideoDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if "Share video" in value:
            return []
        return super().find_elements(by, value)


class StaleClickElement(FakeElement):
    def click(self) -> None:
        raise StaleElementReferenceException("cached post became stale")


class CurrentPostContainerDriver(FakeDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if by == "xpath" and "com.zhiliaoapp.musically:id/efq" in value:
            return self.posts
        if by == "id" and value == "com.zhiliaoapp.musically:id/eqx":
            return []
        if by == "id" and value == "com.zhiliaoapp.musically:id/efq":
            return self.posts
        return super().find_elements(by, value)


class RenamedStableRouteDriver(FakeDriver):
    def __init__(self, *, changes_route: bool = True) -> None:
        super().__init__()
        self.routed = False
        self.changes_route = changes_route
        self.page_source = PROFILE_XML.replace("@sample", "@previous")

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        self.routed = True
        username = "@renamed" if self.changes_route else "@previous"
        self.page_source = PROFILE_XML.replace("@sample", username)

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            text = "@renamed" if self.routed and self.changes_route else "@previous"
            return [FakeElement(text)]
        return super().find_elements(by, value)


class SameStableProfileDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.at_baseline = False
        self.page_source = PROFILE_XML.replace("@sample", "@renamed")

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        self.at_baseline = arguments["url"] == "tiktok://inbox"
        self.page_source = (
            "<hierarchy />"
            if self.at_baseline
            else PROFILE_XML.replace("@sample", "@renamed")
        )

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            return [] if self.at_baseline else [FakeElement("@renamed")]
        return super().find_elements(by, value)


class RestartRequiredStableRouteDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.stable_route_count = 0
        self.baseline_seen = False
        self.restarted = False
        self.terminate_calls = 0
        self.activate_calls = 0
        self.page_source = PROFILE_XML.replace("@sample", "@previous")

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        if arguments["url"] == "tiktok://inbox":
            self.baseline_seen = True
            self.page_source = "<hierarchy />"
        else:
            self.stable_route_count += 1
            if self.restarted:
                self.page_source = PROFILE_XML.replace("@sample", "@renamed")

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            if self.restarted and self.stable_route_count >= 3:
                return [FakeElement("@renamed")]
            if not self.baseline_seen:
                return [FakeElement("@previous")]
            return []
        return super().find_elements(by, value)

    def terminate_app(self, package: str) -> None:
        super().terminate_app(package)
        self.terminate_calls += 1

    def activate_app(self, package: str) -> None:
        super().activate_app(package)
        self.activate_calls += 1
        if self.terminate_calls:
            self.restarted = True


class BaselineStuckUntilRestartDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.restarted = False
        self.at_baseline = False
        self.terminate_calls = 0
        self.activate_calls = 0
        self.page_source = PROFILE_XML.replace("@sample", "@previous")

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        if not self.restarted:
            return
        self.at_baseline = arguments["url"] == "tiktok://inbox"
        self.page_source = (
            "<hierarchy />"
            if self.at_baseline
            else PROFILE_XML.replace("@sample", "@renamed")
        )

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            if self.restarted:
                return [] if self.at_baseline else [FakeElement("@renamed")]
            return [FakeElement("@previous")]
        return super().find_elements(by, value)

    def terminate_app(self, package: str) -> None:
        super().terminate_app(package)
        self.terminate_calls += 1

    def activate_app(self, package: str) -> None:
        super().activate_app(package)
        self.activate_calls += 1
        self.restarted = True


class StableIdBlankUsernameFallbackDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.route_started = False
        self.username_fallback_loaded = False

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        url = arguments["url"]
        if url.startswith("https://www.tiktok.com/@"):
            self.username_fallback_loaded = True
            self.page_source = PROFILE_XML.replace("@sample", "@old_name")
        else:
            self.route_started = True
            self.page_source = "<hierarchy />"

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            if self.username_fallback_loaded:
                return [FakeElement("@old_name")]
            if not self.route_started:
                return [FakeElement("@previous")]
            return []
        return super().find_elements(by, value)


class IncompleteStableRouteDriver(RenamedStableRouteDriver):
    def __init__(self) -> None:
        super().__init__()
        self.page_source = (
            '<hierarchy><node text="@previous" '
            'resource-id="com.zhiliaoapp.musically:id/s7e" /></hierarchy>'
        )

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        FakeDriver.execute_script(self, name, arguments)
        url = arguments["url"]
        username = (
            ""
            if url == "tiktok://inbox"
            else ("@old_name" if url.startswith("https://") else "@renamed")
        )
        self.page_source = (
            f'<hierarchy><node text="{username}" '
            'resource-id="com.zhiliaoapp.musically:id/s7e" /></hierarchy>'
        )

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            username = parse_profile_username(self.page_source)
            return [FakeElement(f"@{username}")] if username else []
        return FakeDriver.find_elements(self, by, value)


class MissingProfileMarkerDriver(FakeDriver):
    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value == "com.zhiliaoapp.musically:id/s7e":
            return []
        return super().find_elements(by, value)


class TerminalProfileDriver(FakeDriver):
    def __init__(self, marker: str) -> None:
        super().__init__()
        self.page_source = f'<node text="{marker}" />'


class StaleTerminalThenValidDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.page_source = (
            '<hierarchy><node text="@previous" '
            'resource-id="com.zhiliaoapp.musically:id/s7e" />'
            '<node text="Account banned" '
            'resource-id="com.zhiliaoapp.musically:id/xcn" />'
            '<node text="This account is no longer available" '
            'resource-id="com.zhiliaoapp.musically:id/message_tv" />'
            "</hierarchy>"
        )

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        self.scripts.append((name, arguments))

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if value in {
            "com.zhiliaoapp.musically:id/s7e",
            "com.zhiliaoapp.musically:id/rgn",
        }:
            username = parse_profile_username(self.page_source)
            return [FakeElement(f"@{username}")] if username else []
        return super().find_elements(by, value)


class TerminalUsernameFallbackDriver(StableIdBlankUsernameFallbackDriver):
    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        if arguments["url"].startswith("https://www.tiktok.com/@"):
            self.page_source = (
                '<hierarchy><node text="@old_name" '
                'resource-id="com.zhiliaoapp.musically:id/s7e" />'
                '<node text="Account banned" '
                'resource-id="com.zhiliaoapp.musically:id/xcn" />'
                '<node text="This account is no longer available" '
                'resource-id="com.zhiliaoapp.musically:id/message_tv" />'
                "</hierarchy>"
            )


class MarkerOnlyTerminalAfterRouteDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.page_source = (
            '<hierarchy><node text="@previous" '
            'resource-id="com.zhiliaoapp.musically:id/s7e" /></hierarchy>'
        )

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        super().execute_script(name, arguments)
        self.page_source = (
            '<hierarchy><node text="Account banned" '
            'resource-id="com.zhiliaoapp.musically:id/xcn" />'
            '<node text="This account is no longer available" '
            'resource-id="com.zhiliaoapp.musically:id/message_tv" /></hierarchy>'
        )


def _stable_target() -> PoolTarget:
    return PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="https://www.tiktok.com/@sample",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )


def test_explicit_banned_profile_is_terminal() -> None:
    driver = TerminalProfileDriver(
        "Account banned - this account is no longer available"
    )
    driver.page_source = (
        '<hierarchy><node text="@sample" '
        'resource-id="com.zhiliaoapp.musically:id/s7e" />'
        '<node text="Account banned" '
        'resource-id="com.zhiliaoapp.musically:id/xcn" />'
        '<node text="This account is no longer available" '
        'resource-id="com.zhiliaoapp.musically:id/message_tv" />'
        "</hierarchy>"
    )
    device = AppiumTikTokDevice(
        driver,
        metric_read_attempts=1,
        poll_interval=0,
    )
    target = _stable_target()

    device.open_target(target)

    with pytest.raises(ProfilePermanentlyUnavailable, match="account banned"):
        device.confirm_profile_identity(target)


def test_blank_profile_is_not_terminal() -> None:
    device = AppiumTikTokDevice(
        TerminalProfileDriver(""), metric_read_attempts=1, poll_interval=0
    )
    target = _stable_target()

    device.open_target(target)

    with pytest.raises(ValueError) as captured:
        device.confirm_profile_identity(target)
    assert not isinstance(captured.value, ProfilePermanentlyUnavailable)


def test_stale_terminal_page_does_not_poison_next_target() -> None:
    driver = StaleTerminalThenValidDriver()
    device = AppiumTikTokDevice(
        driver,
        metric_read_attempts=2,
        poll_interval=0,
        sleeper=lambda _: setattr(driver, "page_source", PROFILE_XML),
    )
    target = _stable_target()
    device._confirmed_profile_username = "older_success"
    device._terminal_page_active = True

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert device._confirmed_profile_username == "sample"


def test_marker_only_terminal_page_is_accepted_after_route_changes() -> None:
    device = AppiumTikTokDevice(
        MarkerOnlyTerminalAfterRouteDriver(), metric_read_attempts=1, poll_interval=0
    )
    target = _stable_target()

    device.open_target(target)

    with pytest.raises(ProfilePermanentlyUnavailable, match="account banned"):
        device.confirm_profile_identity(target)


def test_profile_content_with_terminal_words_is_not_terminal_evidence() -> None:
    driver = FakeDriver()
    driver.page_source = (
        '<hierarchy><node text="@sample" '
        'resource-id="com.zhiliaoapp.musically:id/s7e" />'
        '<node text="12" resource-id="com.zhiliaoapp.musically:id/s5y" />'
        '<node text="Following" resource-id="com.zhiliaoapp.musically:id/s5x" />'
        '<node text="10" resource-id="com.zhiliaoapp.musically:id/s5y" />'
        '<node text="Followers" resource-id="com.zhiliaoapp.musically:id/s5x" />'
        '<node text="Account banned" resource-id="com.example:id/bio" />'
        '<node text="This account is no longer available" '
        'resource-id="com.example:id/caption" /></hierarchy>'
    )
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = _stable_target()

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert device._confirmed_profile_username == "sample"


def test_video_unavailable_message_is_not_terminal_profile_evidence() -> None:
    device = AppiumTikTokDevice(
        TerminalProfileDriver("This video is no longer available"),
        metric_read_attempts=1,
        poll_interval=0,
    )
    target = _stable_target()

    device.open_target(target)

    with pytest.raises(ValueError) as captured:
        device.confirm_profile_identity(target)
    assert not isinstance(captured.value, ProfilePermanentlyUnavailable)


def test_missing_account_heading_with_error_companion_is_terminal() -> None:
    source = (
        '<hierarchy><node text="Couldn\'t find this account" '
        'resource-id="com.zhiliaoapp.musically:id/xcn" />'
        '<node text="Try searching for another account" '
        'resource-id="com.zhiliaoapp.musically:id/message_tv" /></hierarchy>'
    )

    assert _terminal_profile_marker(source) == "couldn't find this account"


def test_localized_terminal_heading_with_error_companion_is_terminal() -> None:
    source = (
        '<hierarchy><node text="账号已注销" '
        'resource-id="com.zhiliaoapp.musically:id/xcn" />'
        '<node text="该账号已不可用" '
        'resource-id="com.zhiliaoapp.musically:id/message_tv" /></hierarchy>'
    )

    assert _terminal_profile_marker(source) == "账号已注销"


def test_username_fallback_preserves_terminal_profile_exception() -> None:
    device = AppiumTikTokDevice(
        TerminalUsernameFallbackDriver(), metric_read_attempts=1, poll_interval=0
    )
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="https://www.tiktok.com/@old_name",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)

    with pytest.raises(ProfilePermanentlyUnavailable, match="account banned"):
        device.confirm_profile_identity(target)


def test_appium_device_opens_profile_with_deep_link() -> None:
    driver = FakeDriver()
    device = AppiumTikTokDevice(driver)

    device.open_profile("Sample")

    assert driver.scripts == [
        (
            "mobile: deepLink",
            {
                "url": "https://www.tiktok.com/@sample",
                "package": "com.zhiliaoapp.musically",
            },
        )
    ]


def test_appium_device_uses_stable_id_route_and_accepts_renamed_profile() -> None:
    driver = RenamedStableRouteDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert driver.scripts[-1][1]["url"] == "snssdk1233://user/profile/123"


def test_appium_device_uses_injected_native_route() -> None:
    driver = RenamedStableRouteDriver()
    routes = []
    device = AppiumTikTokDevice(driver, route_opener=routes.append)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)

    assert routes == ["snssdk1233://user/profile/123"]


def test_appium_device_requires_loaded_profile_surface() -> None:
    driver = IncompleteStableRouteDriver()
    device = AppiumTikTokDevice(
        driver, metric_read_attempts=1, poll_interval=0, action_timeout=0
    )
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)

    with pytest.raises(ValueError, match="profile surface did not become ready"):
        device.confirm_profile_identity(target)


def test_appium_device_rejects_stale_profile_after_stable_route() -> None:
    driver = RenamedStableRouteDriver(changes_route=False)
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)

    with pytest.raises(ValueError, match="stable profile route did not change"):
        device.confirm_profile_identity(target)


def test_appium_device_reloads_same_stable_profile_through_baseline() -> None:
    driver = SameStableProfileDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert [script[1]["url"] for script in driver.scripts] == [
        "snssdk1233://user/profile/123",
        "tiktok://inbox",
        "snssdk1233://user/profile/123",
    ]
    assert device._confirmed_profile_username == "renamed"


def test_appium_device_restarts_once_after_baseline_route_stays_blank() -> None:
    driver = RestartRequiredStableRouteDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert driver.terminate_calls == 1
    assert driver.activate_calls == 1
    assert [script[1]["url"] for script in driver.scripts] == [
        "snssdk1233://user/profile/123",
        "tiktok://inbox",
        "snssdk1233://user/profile/123",
        "tiktok://inbox",
        "snssdk1233://user/profile/123",
    ]


def test_appium_device_restarts_when_inbox_cannot_clear_the_profile() -> None:
    driver = BaselineStuckUntilRestartDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert driver.terminate_calls == 1
    assert driver.activate_calls == 1
    assert [script[1]["url"] for script in driver.scripts] == [
        "snssdk1233://user/profile/123",
        "tiktok://inbox",
        "tiktok://inbox",
        "snssdk1233://user/profile/123",
    ]


def test_appium_device_falls_back_to_exact_username_after_stable_id_stays_blank() -> (
    None
):
    driver = StableIdBlankUsernameFallbackDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=1, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="old_name",
        profile_url="https://www.tiktok.com/@old_name",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)

    assert driver.scripts[-1][1]["url"] == "https://www.tiktok.com/@old_name"


def test_appium_device_reads_metrics_and_clicks_selected_post() -> None:
    driver = FakeDriver()
    device = AppiumTikTokDevice(driver)

    assert device.read_profile_metrics() == ProfileMetrics(12, 10, 4)
    assert device.list_visible_posts() == ("0", "1", "2", "3")

    device.open_post("2")

    assert driver.posts[2].clicked is True


def test_appium_device_accepts_current_post_container_id() -> None:
    driver = CurrentPostContainerDriver()
    device = AppiumTikTokDevice(driver)

    assert device.list_visible_posts() == ("0", "1", "2", "3")
    device.open_post("1")

    assert driver.posts[1].clicked is True


def test_favorite_pixel_state_reads_current_yellow_active_icon() -> None:
    active = BytesIO()
    Image.new("RGB", (2, 2), (255, 205, 0)).save(active, format="PNG")
    inactive = BytesIO()
    Image.new("RGB", (2, 2), (255, 255, 255)).save(inactive, format="PNG")

    assert _favorite_pixel_state(active.getvalue()) is True
    assert _favorite_pixel_state(inactive.getvalue()) is False
    assert _favorite_pixel_state(b"not-png") is None


def test_appium_device_waits_for_profile_metrics() -> None:
    driver = DelayedProfileDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=3, poll_interval=0)

    assert device.read_profile_metrics() == ProfileMetrics(12, 10, 4)
    assert driver.reads == 3


def test_appium_device_retries_deep_link_while_waiting_for_profile_marker() -> None:
    driver = DelayedProfileMarkerDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=4, poll_interval=0)

    device.wait_profile_ready("Sample")

    assert driver.profile_marker_reads == 4
    assert driver.scripts == [
        (
            "mobile: deepLink",
            {
                "url": "https://www.tiktok.com/@sample",
                "package": "com.zhiliaoapp.musically",
            },
        )
    ]


def test_profile_readiness_snapshot_is_reused_for_observation() -> None:
    driver = CachedProfileObservationDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)
    observation = device.read_profile_observation()

    assert observation.observed_username == "sample"
    assert observation.metrics == ProfileMetrics(12, 10, 4)
    assert observation.access_state is ProfileAccessState.PUBLIC
    assert driver.page_source_reads == 1
    assert driver.username_queries == 1


def test_cached_profile_opens_and_verifies_video_with_semantic_elements() -> None:
    driver = BoundedVideoDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)
    device.read_profile_observation()
    assert device.list_video_keys() == ("0", "1", "2", "3")
    device.open_and_confirm_video("2")

    assert driver.posts[2].clicked is True
    assert driver.post_queries == 1
    assert driver.page_source_reads == 1


def test_zero_parsed_posts_use_visible_semantic_video_containers() -> None:
    driver = SemanticOnlyVideoDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)
    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 4)
    assert device.list_video_keys() == ("0", "1", "2", "3")
    assert driver.post_queries == 1


def test_zero_parsed_posts_ignore_hidden_semantic_containers() -> None:
    driver = SemanticOnlyVideoDriver(
        [FakeElement(attributes={"displayed": False}) for _ in range(4)]
    )
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 0)
    assert driver.post_queries == 1


def test_zero_parsed_posts_count_only_visible_semantic_containers() -> None:
    driver = SemanticOnlyVideoDriver(
        [
            FakeElement(),
            FakeElement(attributes={"displayed": False}),
            FakeElement(),
        ]
    )
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 2)
    assert device.list_video_keys() == ("0", "1")
    assert driver.post_queries == 1


def test_zero_parsed_posts_keep_zero_when_semantic_grid_is_empty() -> None:
    driver = SemanticOnlyVideoDriver([])
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 0)
    assert driver.post_queries == 1


def test_zero_parsed_posts_accept_current_semantic_container_in_one_query() -> None:
    driver = CurrentSemanticOnlyVideoDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 4)
    assert driver.post_queries == 1


def test_zero_parsed_posts_skip_semantic_query_when_following_not_greater() -> None:
    driver = SemanticOnlyVideoDriver()
    driver._page_source = driver._page_source.replace(
        '<node text="12" resource-id="com.zhiliaoapp.musically:id/s5y" />',
        '<node text="8" resource-id="com.zhiliaoapp.musically:id/s5y" />',
    )
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(8, 10, 0)
    assert driver.post_queries == 0


def test_zero_parsed_posts_ignore_failed_visibility_checks() -> None:
    driver = SemanticOnlyVideoDriver([FakeElement(), DisplayCheckFailureElement()])
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)

    observation = device.read_profile_observation()

    assert observation.metrics == ProfileMetrics(12, 10, 1)
    assert driver.post_queries == 1


def test_cached_post_stale_click_is_an_explicit_failure() -> None:
    driver = BoundedVideoDriver()
    driver.posts[2] = StaleClickElement()
    device = AppiumTikTokDevice(driver, action_timeout=0)

    assert device.list_video_keys() == ("0", "1", "2", "3")

    with pytest.raises(
        StaleElementReferenceException, match="cached post became stale"
    ):
        device.open_and_confirm_video("2")


def test_cached_post_requires_visible_share_control_after_click() -> None:
    driver = MissingVideoControlDriver()
    device = AppiumTikTokDevice(driver, action_timeout=0)

    assert device.list_video_keys() == ("0", "1", "2", "3")

    with pytest.raises(RuntimeError, match="video controls did not become visible"):
        device.open_and_confirm_video("2")


@pytest.mark.parametrize("invalidate", ["route", "back", "restart", "consume"])
def test_semantic_post_cache_is_scoped_to_one_profile_action(invalidate: str) -> None:
    driver = BoundedVideoDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    original_posts = driver.posts

    assert device.list_visible_posts() == ("0", "1", "2", "3")
    replacement_posts = [FakeElement() for _ in range(4)]
    if invalidate == "route":
        device.open_profile("sample")
    elif invalidate == "back":
        device.return_to_baseline()
    elif invalidate == "restart":
        device.restart_app()
    else:
        device.open_post("0")
    driver.posts = replacement_posts

    device.open_post("0")

    assert replacement_posts[0].clicked is True
    assert original_posts[0].clicked is (invalidate == "consume")


def test_opening_video_invalidates_reusable_profile_snapshot() -> None:
    driver = CachedProfileObservationDriver()
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)
    device.open_post("0")
    device.read_profile_observation()

    assert driver.page_source_reads == 2


def test_confirmed_profile_with_persistently_incomplete_metrics_is_inaccessible() -> (
    None
):
    incomplete = PROFILE_XML.replace(
        '<node text="10" resource-id="com.zhiliaoapp.musically:id/s5y" />\n'
        '  <node text="Followers" resource-id="com.zhiliaoapp.musically:id/s5x" />',
        "",
    )
    driver = CachedProfileObservationDriver(incomplete)
    device = AppiumTikTokDevice(driver, metric_read_attempts=2, poll_interval=0)
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="uid:123",
        target_id="123",
        sec_uid="sec-1",
        username="sample",
        profile_url="",
        source_video_id="",
        source_line_numbers=(2,),
        ordinal=0,
    )

    device.open_target(target)
    device.confirm_profile_identity(target)
    observation = device.read_profile_observation()

    assert observation.observed_username == "sample"
    assert observation.metrics is None
    assert observation.private_account is False
    assert observation.access_state is ProfileAccessState.INACCESSIBLE


def test_appium_device_classifies_profile_identity_mismatch() -> None:
    device = AppiumTikTokDevice(
        WrongProfileMarkerDriver(), metric_read_attempts=1, poll_interval=0
    )
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="sec:1",
        target_id="user-1",
        sec_uid="sec-1",
        username="expected_user",
        profile_url="https://www.tiktok.com/@expected_user",
        source_video_id="video-1",
        source_line_numbers=(2,),
        ordinal=0,
    )

    with pytest.raises(ProfileIdentityMismatch, match="profile mismatch"):
        device.confirm_profile_identity(target)


def test_appium_device_accepts_current_profile_username_marker() -> None:
    device = AppiumTikTokDevice(
        CurrentProfileMarkerDriver(), metric_read_attempts=1, poll_interval=0
    )

    device.wait_profile_ready("sample")


def test_profile_readiness_recovers_from_a_stale_marker_element() -> None:
    device = AppiumTikTokDevice(
        StaleProfileMarkerDriver(), metric_read_attempts=1, poll_interval=0
    )

    device.wait_profile_ready("sample")


def test_appium_device_does_not_classify_a_missing_marker_as_identity_mismatch() -> (
    None
):
    device = AppiumTikTokDevice(
        MissingProfileMarkerDriver(), metric_read_attempts=1, poll_interval=0
    )
    target = PoolTarget(
        pool_id="pool-1",
        identity_key="sec:1",
        target_id="user-1",
        sec_uid="sec-1",
        username="expected_user",
        profile_url="https://www.tiktok.com/@expected_user",
        source_video_id="video-1",
        source_line_numbers=(2,),
        ordinal=0,
    )

    with pytest.raises(ValueError, match="marker is not visible") as captured:
        device.confirm_profile_identity(target)
    assert not isinstance(captured.value, ProfileIdentityMismatch)


def test_appium_device_scrolls_to_confirm_more_than_three_posts() -> None:
    driver = ScrollingProfileDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0)

    metrics = device.read_profile_metrics()

    assert metrics == ProfileMetrics(12, 10, 4)
    assert driver.swipes == 1


def test_appium_device_performs_semantic_video_actions() -> None:
    driver = FakeDriver()
    device = AppiumTikTokDevice(driver, action_delay=0)

    assert device.perform_action("like") is True
    assert device.perform_action("favorite") is True
    assert device.perform_action("share") is True

    assert all(element.clicked for element in driver.action_elements.values())
    assert driver.back_calls == 0


class FakeInboxDriver(FakeDriver):
    def __init__(self) -> None:
        super().__init__()
        self.activity = FakeElement()
        self.new_followers = FakeElement()
        self.follow_backs = [FakeElement(), FakeElement()]
        self.message = FakeElement()
        self.editor = FakeElement()
        self.send = FakeElement()
        self.gestures = []

    def find_elements(self, by: str, value: str):
        if value == '//*[@text="Activity & new followers"]':
            return [self.activity]
        if value == '//*[@text="New followers"]':
            return [self.new_followers]
        if value == '//*[@text="Follow back"]':
            return self.follow_backs
        if value == '//*[@text="Message"]':
            return [self.message]
        if value == '//*[@text="Send" or @content-desc="Send"]':
            return [self.send]
        return super().find_elements(by, value)

    def find_element(self, by: str, value: str):
        if by == "class name" and value == "android.widget.EditText":
            return self.editor
        return super().find_element(by, value)

    def execute_script(self, name: str, arguments: dict) -> None:
        if name == "mobile: clickGesture":
            self.gestures.append(arguments)
            return
        super().execute_script(name, arguments)


def test_appium_device_follows_back_new_followers() -> None:
    driver = FakeInboxDriver()
    device = AppiumTikTokDevice(driver, action_delay=0)

    count = device.follow_back_new_followers(limit=2)

    assert count == 2
    assert driver.activity.clicked is True
    assert driver.new_followers.clicked is True
    assert all(button.clicked for button in driver.follow_backs)


def test_appium_device_greets_one_new_follower_after_follow_back() -> None:
    driver = FakeInboxDriver()
    device = AppiumTikTokDevice(driver, action_delay=0)

    sent = device.greet_one_new_follower(lambda _: "Hello from AI")

    assert sent is True
    assert driver.follow_backs[0].clicked is True
    assert driver.gestures == [{"x": 270, "y": 740}]
    assert driver.message.clicked is True
    assert driver.editor.value == "Hello from AI"
    assert driver.send.clicked is True


class UnverifiedLikeDriver(FakeDriver):
    def find_elements(self, by: str, value: str):
        if "Video liked" in value or "Unlike video" in value:
            return []
        return super().find_elements(by, value)


def test_appium_device_rejects_unverified_like() -> None:
    device = AppiumTikTokDevice(
        UnverifiedLikeDriver(), action_delay=0, action_timeout=0
    )

    with pytest.raises(RuntimeError, match="like action was not verified"):
        device.perform_action("like")


class SemanticElement(FakeElement):
    def __init__(self, label: str, callback=None, attributes=None) -> None:
        super().__init__(label)
        self.label = label
        self.callback = callback
        self.attributes = attributes or {}

    def click(self) -> None:
        super().click()
        if self.callback is not None:
            self.callback()

    def get_attribute(self, name: str):
        value = self.attributes.get(name)
        return value() if callable(value) else value


class SemanticActionDriver:
    def __init__(self, *, delayed_like_reads: int = 0) -> None:
        self.clicked_labels: list[str] = []
        self.semantic_queries: list[str] = []
        self.liked = False
        self.favorite = False
        self.share_open = False
        self.reposted = False
        self.delayed_like_reads = delayed_like_reads
        self.like_active_reads = 0
        self.like = SemanticElement("Like", self._click_like)
        self.favorite_control = SemanticElement(
            "Favorite",
            self._click_favorite,
            {"selected": lambda: "true" if self.favorite else "false"},
        )
        self.share = SemanticElement("Share", self._click_share)
        self.repost = SemanticElement("Repost", self._click_repost, {"text": "Repost"})

    @property
    def page_source(self) -> str:
        return "<hierarchy />"

    def _click_like(self) -> None:
        self.clicked_labels.append("Like")
        self.liked = True

    def _click_favorite(self) -> None:
        self.clicked_labels.append("Favorite")
        self.favorite = True

    def _click_share(self) -> None:
        self.clicked_labels.append("Share")
        self.share_open = True

    def _click_repost(self) -> None:
        self.clicked_labels.append("Repost")
        self.reposted = True

    def find_elements(self, by: str, value: str):
        self.semantic_queries.append(value)
        if "Video liked" in value and "Like video" in value:
            return [SemanticElement("Video liked")] if self.liked else [self.like]
        if "Remove from Favorites" in value and "Add or remove" in value:
            return (
                [SemanticElement("Added to Favorites")]
                if self.favorite
                else [self.favorite_control]
            )
        if "You reposted" in value and "Share video" in value:
            if self.reposted:
                return [SemanticElement("You reposted")]
            if self.share_open:
                return [self.repost]
            return [self.share]
        if "Video liked" in value or "Unlike video" in value:
            if self.liked:
                self.like_active_reads += 1
            return (
                [SemanticElement("Video liked")]
                if self.liked and self.like_active_reads > self.delayed_like_reads
                else []
            )
        if "Like video" in value:
            return [] if self.liked else [self.like]
        if "Remove from Favorites" in value or "Added to Favorites" in value:
            return [SemanticElement("Added to Favorites")] if self.favorite else []
        if "Favorites" in value:
            return [self.favorite_control]
        if "You reposted" in value or "Remove repost" in value:
            return [SemanticElement("You reposted")] if self.reposted else []
        if "Share video" in value:
            return [self.share]
        if "Repost" in value and self.share_open:
            return [self.repost]
        return []

    def find_element(self, by: str, value: str):
        elements = self.find_elements(by, value)
        if not elements:
            raise LookupError(value)
        return elements[0]


class RepostUnavailableDriver(SemanticActionDriver):
    def find_elements(self, by: str, value: str):
        if "You reposted" in value and "Share video" in value:
            if self.share_open:
                self.semantic_queries.append(value)
                return []
            return super().find_elements(by, value)
        if "Repost" in value:
            return []
        if self.share_open and (
            'content-desc="Bottom sheet"' in value or "Copy link" in value
        ):
            return [SemanticElement("Share surface")]
        return super().find_elements(by, value)


class AmbiguousSemanticActionDriver(SemanticActionDriver):
    def __init__(self, ambiguous_outcome: OutcomeKind) -> None:
        super().__init__()
        self.ambiguous_outcome = ambiguous_outcome

    def find_elements(self, by: str, value: str):
        elements = super().find_elements(by, value)
        matches_outcome = {
            OutcomeKind.LIKE: "Video liked" in value and "Like video" in value,
            OutcomeKind.FAVORITE: (
                "Remove from Favorites" in value and "Add or remove" in value
            ),
            OutcomeKind.REPOST: "You reposted" in value and "Share video" in value,
        }
        if matches_outcome[self.ambiguous_outcome] and elements:
            return [elements[0], elements[0]]
        return elements


class HiddenRepostUnavailableDriver(RepostUnavailableDriver):
    def find_elements(self, by: str, value: str):
        if self.share_open and (
            'content-desc="Bottom sheet"' in value or "Copy link" in value
        ):
            return [
                SemanticElement("Hidden share surface", attributes={"displayed": False})
            ]
        return super().find_elements(by, value)


class SteppingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


def test_execute_like_waits_for_delayed_selected_state() -> None:
    driver = SemanticActionDriver(delayed_like_reads=2)
    device = AppiumTikTokDevice(
        driver,
        poll_interval=0,
        action_timeout=2,
        clock=SteppingClock(),
        sleeper=lambda _: None,
    )

    assert device.execute_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Like"]


def test_execute_like_uses_one_query_before_and_after_click() -> None:
    driver = SemanticActionDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED

    assert len(driver.semantic_queries) == 2


def test_execute_like_keeps_duplicate_controls_uncertain() -> None:
    driver = AmbiguousSemanticActionDriver(OutcomeKind.LIKE)
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.LIKE) is ActionResult.UNCERTAIN
    assert driver.clicked_labels == []


def test_execute_favorite_requires_semantic_selected_state() -> None:
    driver = SemanticActionDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.FAVORITE) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Favorite"]


def test_execute_favorite_uses_one_query_before_and_after_click() -> None:
    driver = SemanticActionDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.FAVORITE) is ActionResult.CONFIRMED

    assert len(driver.semantic_queries) == 2


def test_execute_favorite_keeps_duplicate_controls_uncertain() -> None:
    driver = AmbiguousSemanticActionDriver(OutcomeKind.FAVORITE)
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.FAVORITE) is ActionResult.UNCERTAIN
    assert driver.clicked_labels == []


def test_execute_repost_clicks_share_then_repost_and_verifies_state() -> None:
    driver = SemanticActionDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Share", "Repost"]


def test_execute_repost_consolidates_semantic_state_queries() -> None:
    driver = SemanticActionDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.CONFIRMED

    assert len(driver.semantic_queries) == 3


def test_execute_repost_keeps_duplicate_controls_uncertain() -> None:
    driver = AmbiguousSemanticActionDriver(OutcomeKind.REPOST)
    driver.share_open = True
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.UNCERTAIN
    assert driver.clicked_labels == []


def test_execute_repost_reports_visible_unavailable_share_surface() -> None:
    driver = RepostUnavailableDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.REPOST).value == "unavailable"
    assert driver.clicked_labels == ["Share"]


def test_execute_repost_keeps_hidden_share_surface_uncertain() -> None:
    driver = HiddenRepostUnavailableDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.UNCERTAIN
    assert driver.clicked_labels == ["Share"]


def test_reconcile_liked_video_does_not_click_again() -> None:
    driver = SemanticActionDriver()
    driver.liked = True
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.reconcile_outcome(OutcomeKind.LIKE) is ActionResult.CONFIRMED
    assert driver.clicked_labels == []


def test_repost_resume_uses_already_open_share_surface() -> None:
    driver = SemanticActionDriver()
    driver.share_open = True
    device = AppiumTikTokDevice(driver, poll_interval=0, action_timeout=0)

    assert device.reconcile_outcome(OutcomeKind.REPOST) is ActionResult.NOT_APPLIED
    assert device.execute_outcome(OutcomeKind.REPOST) is ActionResult.CONFIRMED
    assert driver.clicked_labels == ["Repost"]

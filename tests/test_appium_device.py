from tests.test_profile_parser import PROFILE_XML
import pytest
from tikpoc.device import AppiumTikTokDevice
from tikpoc.models import ProfileMetrics


class FakeElement:
    def __init__(self, text: str = "") -> None:
        self.clicked = False
        self.value = ""
        self.text = text
        self.rect = {"x": 800, "y": 700, "width": 200, "height": 80}

    def click(self) -> None:
        self.clicked = True

    def send_keys(self, value: str) -> None:
        self.value = value

    @property
    def screenshot_as_png(self) -> bytes:
        return b"after" if self.clicked else b"before"


class FakeDriver:
    def __init__(self) -> None:
        self.page_source = PROFILE_XML
        self.scripts: list[tuple[str, dict[str, str]]] = []
        self.posts = [FakeElement(), FakeElement(), FakeElement(), FakeElement()]
        self.action_elements = {
            '//*[starts-with(@content-desc, "Like video.")]': FakeElement(),
            '//*[@content-desc="Add or remove this video from Favorites."]/..': FakeElement(),
            '//*[starts-with(@content-desc, "Share video.")]': FakeElement(),
            '//*[@text="Repost" or @content-desc="Repost"]': FakeElement(),
        }
        self.back_calls = 0

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        self.scripts.append((name, arguments))

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        if by == "id":
            assert value == "com.zhiliaoapp.musically:id/eqx"
            return self.posts
        if value == '//*[@content-desc="Video liked"]':
            return [FakeElement()]
        if "You reposted" in value:
            return [FakeElement()]
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


def test_appium_device_reads_metrics_and_clicks_selected_post() -> None:
    driver = FakeDriver()
    device = AppiumTikTokDevice(driver)

    assert device.read_profile_metrics() == ProfileMetrics(12, 10, 4)
    assert device.list_visible_posts() == ("0", "1", "2", "3")

    device.open_post("2")

    assert driver.posts[2].clicked is True


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
        if value == '//*[@content-desc="Video liked"]':
            return []
        return super().find_elements(by, value)


def test_appium_device_rejects_unverified_like() -> None:
    device = AppiumTikTokDevice(UnverifiedLikeDriver(), action_delay=0)

    with pytest.raises(RuntimeError, match="like action was not verified"):
        device.perform_action("like")

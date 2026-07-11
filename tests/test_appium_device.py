from tests.test_profile_parser import PROFILE_XML
from tikpoc.device import AppiumTikTokDevice
from tikpoc.models import ProfileMetrics


class FakeElement:
    def __init__(self) -> None:
        self.clicked = False

    def click(self) -> None:
        self.clicked = True


class FakeDriver:
    def __init__(self) -> None:
        self.page_source = PROFILE_XML
        self.scripts: list[tuple[str, dict[str, str]]] = []
        self.posts = [FakeElement(), FakeElement(), FakeElement(), FakeElement()]

    def execute_script(self, name: str, arguments: dict[str, str]) -> None:
        self.scripts.append((name, arguments))

    def find_elements(self, by: str, value: str) -> list[FakeElement]:
        assert by == "id"
        assert value == "com.zhiliaoapp.musically:id/eqx"
        return self.posts

    def activate_app(self, package: str) -> None:
        assert package == "com.zhiliaoapp.musically"

    def terminate_app(self, package: str) -> None:
        assert package == "com.zhiliaoapp.musically"

    def back(self) -> None:
        return None

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


def test_appium_device_scrolls_to_confirm_more_than_three_posts() -> None:
    driver = ScrollingProfileDriver()
    device = AppiumTikTokDevice(driver, poll_interval=0)

    metrics = device.read_profile_metrics()

    assert metrics == ProfileMetrics(12, 10, 4)
    assert driver.swipes == 1

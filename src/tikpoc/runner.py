import time
from pathlib import Path

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig

from .db import Database
from .device import AppiumTikTokDevice, TIKTOK_PACKAGE
from .interactions import InteractionPolicy
from .messaging import AiReplyClient
from .worker import Worker


def should_wait_when_idle(*, event_driven: bool, control: str) -> bool:
    return event_driven and control == "running"


def _myt_slot_offset(udid: str) -> int | None:
    try:
        port = int(str(udid).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None
    offset, remainder = divmod(port - 30_000, 100)
    return offset if 0 <= offset < 100 and remainder == 0 else None


def create_driver(appium_url: str, udid: str, *, command_timeout: int = 20):
    capabilities = {
        "platformName": "Android",
        "appium:automationName": "UiAutomator2",
        "appium:udid": udid,
        "appium:deviceName": udid,
        "appium:appPackage": TIKTOK_PACKAGE,
        "appium:noReset": True,
        "appium:newCommandTimeout": 3600,
    }
    slot_offset = _myt_slot_offset(udid)
    if slot_offset is not None:
        capabilities.update(
            {
                "appium:systemPort": 8200 + slot_offset,
                "appium:mjpegServerPort": 9100 + slot_offset,
            }
        )
    options = UiAutomator2Options().load_capabilities(capabilities)
    driver = webdriver.Remote(
        appium_url,
        options=options,
        client_config=AppiumClientConfig(appium_url, timeout=command_timeout),
    )
    driver.update_settings({"ignoreUnimportantViews": True, "waitForIdleTimeout": 0})
    return driver


def run_queue(
    database_path: Path,
    appium_url: str,
    udid: str,
    *,
    idle_sleep: float = 2.0,
    restart_interval: float = 3600.0,
    interaction_policy: InteractionPolicy | None = None,
    device_id: str = "default",
    event_driven: bool = False,
) -> None:
    database = Database(database_path)
    database.migrate()
    database.recover_stale_tasks()
    database.recover_stale_device_events()
    driver = create_driver(appium_url, udid)
    device = AppiumTikTokDevice(driver)
    worker = Worker(
        database, device, interaction_policy=interaction_policy, device_id=device_id
    )
    reply_client = AiReplyClient.from_environment() if event_driven else None
    last_restart = time.monotonic()
    database.record_runtime_event("worker_started")
    try:
        while True:
            control = database.worker_control()
            if control == "stopped":
                database.record_runtime_event("worker_stopped")
                break
            if control == "paused":
                time.sleep(idle_sleep)
                continue
            if time.monotonic() - last_restart >= restart_interval:
                device.restart_app()
                database.record_runtime_event("app_restarted")
                last_restart = time.monotonic()
            if event_driven:
                event = database.claim_device_event(device_id)
            else:
                event = None
            if event is not None:
                try:
                    if event.event_type == "new_follower":
                        handled = device.greet_one_new_follower(reply_client.reply)
                    elif event.event_type == "dm_received":
                        handled = device.reply_to_inbox_event(
                            str(event.payload.get("title") or ""),
                            str(event.payload.get("message") or ""),
                            reply_client.reply,
                        )
                    else:
                        handled = False
                    database.finish_device_event(
                        event.id,
                        handled,
                        error_code=None if handled else "handler_returned_false",
                    )
                    database.record_runtime_event(
                        f"device_event_{event.event_type}_{int(handled)}"
                    )
                except Exception as error:
                    database.finish_device_event(
                        event.id, False, error_code=type(error).__name__
                    )
                    database.record_runtime_event(
                        f"device_event_error_{type(error).__name__}"
                    )
                continue
            if not worker.run_one(record_empty=not event_driven):
                control = database.worker_control()
                if should_wait_when_idle(event_driven=event_driven, control=control):
                    time.sleep(idle_sleep)
                    continue
                if control == "running":
                    break
                time.sleep(idle_sleep)
    finally:
        driver.quit()
        database.record_runtime_event("worker_exited")

import time
from pathlib import Path

from appium import webdriver
from appium.options.android import UiAutomator2Options

from .db import Database
from .device import AppiumTikTokDevice, TIKTOK_PACKAGE
from .worker import Worker


def create_driver(appium_url: str, udid: str):
    options = UiAutomator2Options().load_capabilities(
        {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:udid": udid,
            "appium:deviceName": udid,
            "appium:appPackage": TIKTOK_PACKAGE,
            "appium:noReset": True,
            "appium:newCommandTimeout": 3600,
        }
    )
    return webdriver.Remote(appium_url, options=options)


def run_queue(
    database_path: Path,
    appium_url: str,
    udid: str,
    *,
    idle_sleep: float = 2.0,
    restart_interval: float = 3600.0,
) -> None:
    database = Database(database_path)
    database.migrate()
    database.recover_stale_tasks()
    driver = create_driver(appium_url, udid)
    device = AppiumTikTokDevice(driver)
    worker = Worker(database, device)
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
            if not worker.run_one():
                if database.worker_control() == "running":
                    break
                time.sleep(idle_sleep)
    finally:
        driver.quit()
        database.record_runtime_event("worker_exited")

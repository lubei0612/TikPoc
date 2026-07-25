from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from .publishing_db import PublishingJob


@dataclass(frozen=True)
class RoxyConnection:
    profile_name: str
    dir_id: str
    debugger_address: str
    driver_path: str


@dataclass(frozen=True)
class StagedPhotoPost:
    job_id: int
    account_id: str
    expected_username: str
    profile_name: str
    title: str
    caption: str
    asset_count: int
    page_url: str


class RoxyApiClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_host: str = "http://127.0.0.1:50000",
        opener: Callable = urlopen,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ROXY_API_KEY is required")
        self._api_key = api_key.strip()
        self.api_host = api_host.rstrip("/")
        self.opener = opener

    def __repr__(self) -> str:
        return f"RoxyApiClient(api_host={self.api_host!r}, api_key=<configured>)"

    @classmethod
    def from_env_file(cls, path: Path) -> RoxyApiClient:
        values: dict[str, str] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
        return cls(
            api_key=values.get("ROXY_API_KEY", ""),
            api_host=values.get("ROXY_API_HOST", "http://127.0.0.1:50000"),
        )

    def connections(self) -> tuple[RoxyConnection, ...]:
        payload = self._request("GET", "/browser/connection_info")
        rows = payload.get("data") or []
        return tuple(
            RoxyConnection(
                profile_name=str(row.get("windowName") or ""),
                dir_id=str(row.get("dirId") or ""),
                debugger_address=str(row.get("http") or ""),
                driver_path=str(row.get("driver") or ""),
            )
            for row in rows
            if isinstance(row, dict)
            and row.get("dirId")
            and row.get("http")
            and row.get("driver")
        )

    def open_profile(
        self, *, workspace_id: int, dir_id: str, force_open: bool = False
    ) -> None:
        self._request(
            "POST",
            "/browser/open",
            {
                "workspaceId": workspace_id,
                "dirId": dir_id,
                "forceOpen": force_open,
            },
        )

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        request = Request(
            f"{self.api_host}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"token": self._api_key, "Content-Type": "application/json"},
            method=method,
        )
        with self.opener(request, timeout=30) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise TypeError("RoxyBrowser response must be an object")
        if payload.get("code") != 0:
            raise RuntimeError(str(payload.get("msg") or "RoxyBrowser request failed"))
        return payload


class RoxyTikTokStudioPublisher:
    def stage(
        self,
        connection: RoxyConnection,
        *,
        expected_username: str,
        job: PublishingJob,
    ) -> StagedPhotoPost:
        paths = tuple(str(Path(path).resolve()) for path in job.asset_paths)
        if not paths or any(not Path(path).is_file() for path in paths):
            raise ValueError("all publishing assets must exist")
        driver, service = _attach(connection)
        try:
            uploader_handle = _open_uploader(driver)
            visible_username = _read_visible_username(driver, uploader_handle)
            validate_visible_username(expected_username, visible_username)
            driver.switch_to.window(uploader_handle)
            _select_photo_mode(driver)
            image_input = WebDriverWait(driver, 15).until(
                lambda current: current.find_element(
                    By.CSS_SELECTOR, 'input[type="file"][accept*="image"]'
                )
            )
            image_input.send_keys("\n".join(paths))
            WebDriverWait(driver, 45).until(
                lambda current: "/tiktokstudio/upload/post/photo" in current.current_url
            )
            title = photo_title(job)
            title_input = WebDriverWait(driver, 20).until(
                lambda current: current.find_element(
                    By.CSS_SELECTOR, 'input[placeholder="Add a catchy title"]'
                )
            )
            title_input.clear()
            title_input.send_keys(title)
            description = driver.find_element(
                By.CSS_SELECTOR, '[contenteditable="true"].public-DraftEditor-content'
            )
            description.click()
            description.send_keys(Keys.COMMAND, "a")
            description.send_keys(job.caption)
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if expected_username.casefold().lstrip("@") not in body_text.casefold():
                raise ValueError("TikTok Studio identity disappeared after upload")
            return StagedPhotoPost(
                job_id=job.job_id,
                account_id=job.account_id,
                expected_username=expected_username.lstrip("@").casefold(),
                profile_name=connection.profile_name,
                title=title,
                caption=job.caption,
                asset_count=len(paths),
                page_url=driver.current_url,
            )
        finally:
            service.stop()

    def submit_staged(
        self,
        connection: RoxyConnection,
        *,
        expected_username: str,
    ) -> str:
        driver, service = _attach(connection)
        try:
            staged_handles = []
            for handle in driver.window_handles:
                driver.switch_to.window(handle)
                if "/tiktokstudio/upload/post/photo" in driver.current_url:
                    staged_handles.append(handle)
            if len(staged_handles) != 1:
                raise ValueError("expected exactly one staged TikTok photo post")
            driver.switch_to.window(staged_handles[0])
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if expected_username.casefold().lstrip("@") not in body_text.casefold():
                raise ValueError("TikTok Studio identity mismatch before submit")
            buttons = driver.find_elements(
                By.XPATH, "//button[normalize-space(text())='Post']"
            )
            visible = [button for button in buttons if button.is_displayed()]
            if len(visible) != 1 or not visible[0].is_enabled():
                raise ValueError("expected one enabled Post button")
            visible[0].click()
            WebDriverWait(driver, 45).until(
                lambda current: (
                    "/tiktokstudio/upload/post/photo" not in current.current_url
                    or "uploaded"
                    in current.find_element(By.TAG_NAME, "body").text.casefold()
                )
            )
            links = driver.find_elements(
                By.CSS_SELECTOR,
                f'a[href*="/@{expected_username.lstrip("@").casefold()}/video/"]',
            )
            return links[0].get_attribute("href") if len(links) == 1 else ""
        finally:
            service.stop()


def expected_username_from_profile_url(url: str) -> str:
    match = re.search(r"/@([^/?#]+)", urlparse(url).path)
    if not match:
        raise ValueError("TikTok profile URL does not contain a username")
    return match.group(1).casefold()


def validate_visible_username(expected: str, visible: str) -> None:
    if expected.lstrip("@").casefold() != visible.lstrip("@").casefold():
        raise ValueError("TikTok identity mismatch")


def photo_title(job: PublishingJob) -> str:
    lead = job.caption.split("Interested in this style?", 1)[0].strip()
    if len(lead) <= 90:
        return lead
    shortened = lead[:90].rsplit(" ", 1)[0].rstrip(" ,;:-.")
    return shortened


def _attach(connection: RoxyConnection):
    options = webdriver.ChromeOptions()
    options.debugger_address = connection.debugger_address
    service = Service(connection.driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver, service


def _open_uploader(driver) -> str:
    before = set(driver.window_handles)
    driver.execute_script(
        "window.open('https://www.tiktok.com/tiktokstudio/upload?lang=en','_blank')"
    )
    WebDriverWait(driver, 10).until(
        lambda current: len(current.window_handles) > len(before)
    )
    handle = next(iter(set(driver.window_handles) - before))
    driver.switch_to.window(handle)
    WebDriverWait(driver, 30).until(
        lambda current: "tiktokstudio/upload" in current.current_url
    )
    return handle


def _read_visible_username(driver, uploader_handle: str) -> str:
    driver.switch_to.window(uploader_handle)
    profile_items = driver.find_elements(
        By.XPATH, "//*[normalize-space(text())='Profile']"
    )
    if not profile_items:
        avatar = WebDriverWait(driver, 15).until(
            lambda current: current.find_element(
                By.CSS_SELECTOR, '[data-tt="components_Avatar_Absolute"]'
            )
        )
        avatar.click()
        profile_items = WebDriverWait(driver, 10).until(
            lambda current: current.find_elements(
                By.XPATH, "//*[normalize-space(text())='Profile']"
            )
        )
    visible = [item for item in profile_items if item.is_displayed()]
    if len(visible) != 1:
        raise ValueError("expected one visible TikTok Studio profile action")
    before = set(driver.window_handles)
    visible[0].click()
    WebDriverWait(driver, 15).until(
        lambda current: (
            current.current_url.startswith("https://www.tiktok.com/@")
            or len(current.window_handles) > len(before)
        )
    )
    new_handles = set(driver.window_handles) - before
    profile_handle = (
        next(iter(new_handles)) if new_handles else driver.current_window_handle
    )
    driver.switch_to.window(profile_handle)
    WebDriverWait(driver, 15).until(
        lambda current: current.current_url.startswith("https://www.tiktok.com/@")
    )
    username = expected_username_from_profile_url(driver.current_url)
    if profile_handle != uploader_handle:
        driver.close()
    driver.switch_to.window(uploader_handle)
    return username


def _select_photo_mode(driver) -> None:
    candidates = driver.find_elements(By.XPATH, "//*[normalize-space(text())='Photos']")
    visible = [candidate for candidate in candidates if candidate.is_displayed()]
    if len(visible) != 1:
        raise ValueError("expected one visible Photos upload tab")
    visible[0].click()

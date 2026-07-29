from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from .device import TIKTOK_PACKAGE
from .profile_parser import parse_profile_username, parse_visible_post_keys
from .publishing_db import PublishingJob, PublishingRepository


class IdentityMismatch(RuntimeError):
    pass


class VerificationRequired(RuntimeError):
    pass


CREATE_XPATH = (
    '//*[@content-desc="Create" or @text="Create" or @content-desc="创建" '
    'or @text="创建" or @content-desc="+"]'
)
UPLOAD_XPATH = (
    '//*[@content-desc="Upload" or @text="Upload" or @content-desc="上传" '
    'or @text="上传"]'
)
ALBUM_LAUNCH_XPATH = (
    f'//*[@resource-id="{TIKTOK_PACKAGE}:id/cwd" or '
    f'@resource-id="{TIKTOK_PACKAGE}:id/view_bg2" or '
    f'@resource-id="{TIKTOK_PACKAGE}:id/c55"]'
)
PHOTOS_XPATH = (
    '//*[@text="Photos" or @content-desc="Photos" or @text="照片" '
    'or @content-desc="照片"]'
)
ALBUM_XPATH = (
    '//*[@text="All" '
    'or @text="Recents" or @text="Albums" or @text="相册" '
    'or @content-desc="All" or @content-desc="Recents"]'
)
ALBUM_TITLE_XPATH = (
    f'//*[@resource-id="{TIKTOK_PACKAGE}:id/cq0" or '
    f'@resource-id="{TIKTOK_PACKAGE}:id/dna" or '
    f'@resource-id="{TIKTOK_PACKAGE}:id/vzq"]'
)
MULTI_SELECT_XPATH = (
    '//*[@text="Select multiple" or @content-desc="Select multiple" '
    'or @text="选择多个" or @content-desc="选择多个"]'
)
MULTI_SELECT_CONTROL_XPATH = f'//*[@resource-id="{TIKTOK_PACKAGE}:id/lz2"]'
THUMBNAIL_CHECK_XPATH = (
    f'//*[@resource-id="{TIKTOK_PACKAGE}:id/ken" or '
    '@resource-id="com.android.providers.media.module:id/icon_check" '
    'or contains(@resource-id,"select_check") or contains(@resource-id,"checkbox")]'
    " | //android.widget.GridView[1]/android.widget.FrameLayout/"
    "android.widget.FrameLayout/android.widget.Button"
)
THUMBNAIL_TILE_XPATH = "//android.widget.GridView[1]/android.widget.FrameLayout"
NEXT_XPATH = (
    f'//*[@resource-id="{TIKTOK_PACKAGE}:id/wpz" or @text="Next" '
    'or @content-desc="Next" or @text="下一步" '
    'or @content-desc="下一步"]'
)
GALLERY_NEXT_XPATH = f'//*[@resource-id="{TIKTOK_PACKAGE}:id/sye"]'
EDITOR_NEXT_XPATH = (
    f'//*[@resource-id="{TIKTOK_PACKAGE}:id/p8v" or '
    f'@resource-id="{TIKTOK_PACKAGE}:id/p8x" or @text="Next"]'
)
CAPTION_XPATH = "//android.widget.EditText"
FINAL_CAPTION_XPATH = f'//*[@resource-id="{TIKTOK_PACKAGE}:id/fe7"]'
POST_XPATH = (
    '//*[@text="Post" or @content-desc="Post" or @text="发布" or @content-desc="发布"]'
)
FINAL_POST_XPATH = f'//*[@resource-id="{TIKTOK_PACKAGE}:id/p3i"]'
PUBLISH_CONFIRM_XPATH = (
    '//*[@text="Publish now" or @content-desc="Publish now" '
    'or @text="立即发布" or @content-desc="立即发布"]'
)
PROFILE_TILE_XPATH = f'//*[@resource-id="{TIKTOK_PACKAGE}:id/dp6"]'
VERIFICATION_MARKERS = (
    "verify to continue",
    "verification required",
    "captcha",
    "drag the puzzle",
    "complete the puzzle",
    "请完成验证",
    "安全验证",
)
PERMISSION_XPATH = (
    '//*[@text="ALLOW ALL" or @text="WHILE USING THE APP" '
    'or @text="ALLOW" or @text="允许全部" or @text="使用应用时允许"]'
)
PUBLISH_ACTIVITY = (
    "com.zhiliaoapp.musically/"
    "com.ss.android.ugc.aweme.shortvideo.ui.VideoRecordPermissionActivity"
)


class AppiumTikTokPhotoUi:
    def __init__(
        self,
        driver: object,
        *,
        timeout: float = 10,
        poll_interval: float = 0.25,
        post_submit_grace: float = 10.0,
        sleeper: Callable[[float], None] = time.sleep,
        activity_opener: Callable[[], None] | None = None,
    ) -> None:
        self.driver = driver
        self.timeout = max(0.0, timeout)
        self.poll_interval = max(0.0, poll_interval)
        self.post_submit_grace = max(0.0, post_submit_grace)
        self.sleeper = sleeper
        self.activity_opener = activity_opener
        self.expected_username = ""
        self._submitted = False

    def verify_identity(self, expected_username: str) -> None:
        expected = expected_username.strip().removeprefix("@").lower()
        if not expected:
            raise ValueError("expected TikTok username is required")
        self.driver.execute_script(
            "mobile: deepLink",
            {
                "url": f"https://www.tiktok.com/@{expected}",
                "package": TIKTOK_PACKAGE,
            },
        )
        actual = self._wait_profile_username()
        if actual != expected:
            raise IdentityMismatch(
                f"TikTok identity mismatch: expected @{expected}, got @{actual or 'unknown'}"
            )
        self.expected_username = expected

    def snapshot_posts(self) -> frozenset[str]:
        return self._visible_post_keys(str(self.driver.page_source)) | self._tile_keys()

    def prepare(self, remote_paths: tuple[str, ...], caption: str) -> None:
        if not self.expected_username:
            raise RuntimeError("TikTok identity must be verified before preparation")
        if not remote_paths or len(remote_paths) > 35:
            raise ValueError("a photo post requires between 1 and 35 images")
        parents = {str(Path(path).parent) for path in remote_paths}
        if len(parents) != 1:
            raise ValueError("all publishing images must come from one isolated album")
        album_name = Path(next(iter(parents))).name
        if not album_name.startswith("job-"):
            raise ValueError("publishing album must be job-scoped")
        create = self._first_now(CREATE_XPATH)
        if create is not None:
            create.click()
        else:
            if self.activity_opener is not None:
                self.activity_opener()
            else:
                self.driver.execute_script(
                    "mobile: startActivity", {"intent": PUBLISH_ACTIVITY}
                )
        self._allow_permissions()
        self._dismiss_onboarding()
        upload = self._first_now(UPLOAD_XPATH)
        if upload is not None:
            upload.click()
            photos = self._first_now(PHOTOS_XPATH)
            if photos is not None:
                photos.click()
        else:
            photos = self._first_now(PHOTOS_XPATH)
            if photos is not None:
                photos.click()
            self._click_required(ALBUM_LAUNCH_XPATH, "photo album control")
        self._allow_permissions()
        album = self._first(ALBUM_TITLE_XPATH)
        if album is None:
            album = self._first(ALBUM_XPATH)
        if album is not None:
            album.click()
        escaped_album = album_name.replace('"', "")
        job_album_xpath = (
            f'//*[@text="{escaped_album}" or @content-desc="{escaped_album}"]'
        )
        job_album = self._first_now(job_album_xpath)
        if job_album is None:
            size = self.driver.get_window_size()
            for _ in range(10):
                self.driver.execute_script(
                    "mobile: swipeGesture",
                    {
                        "left": 0,
                        "top": round(int(size["height"]) * 0.12),
                        "width": int(size["width"]),
                        "height": round(int(size["height"]) * 0.76),
                        "direction": "up",
                        "percent": 0.75,
                    },
                )
                self.sleeper(0.25)
                job_album = self._first_now(job_album_xpath)
                if job_album is not None:
                    break
        if job_album is None:
            raise RuntimeError("isolated job album is not visible")
        job_album.click()
        multi = self._first(MULTI_SELECT_CONTROL_XPATH)
        if multi is None:
            multi = self._first(MULTI_SELECT_XPATH)
        if multi is not None:
            multi.click()
            self.sleeper(0.5)
            if self._first_now(THUMBNAIL_CHECK_XPATH) is None:
                multi = self._first(MULTI_SELECT_CONTROL_XPATH)
                if multi is None:
                    multi = self._first(MULTI_SELECT_XPATH)
                if multi is not None:
                    multi.click()
                    self.sleeper(0.5)
        gallery_tiles = self.driver.find_elements(By.XPATH, THUMBNAIL_TILE_XPATH)
        thumbnails = self._wait_elements(THUMBNAIL_CHECK_XPATH)
        if max(len(gallery_tiles), len(thumbnails)) < len(remote_paths):
            raise RuntimeError(
                f"isolated album has {max(len(gallery_tiles), len(thumbnails))} "
                "selectable images; "
                f"expected {len(remote_paths)}"
            )
        for index in range(len(remote_paths)):
            if len(gallery_tiles) >= len(remote_paths):
                self._select_gallery_tile(index)
                self.sleeper(0.3)
                continue
            refreshed = self._wait_elements(THUMBNAIL_CHECK_XPATH)
            if len(refreshed) <= index:
                raise RuntimeError(
                    "isolated album changed while selecting publishing images"
                )
            refreshed[index].click()
            self.sleeper(0.3)
        self.sleeper(1.0)
        gallery_next = self._first(GALLERY_NEXT_XPATH)
        if gallery_next is not None:
            gallery_next.click()
        else:
            self._click_required(NEXT_XPATH, "Next control")
        caption_input = self._first_now(FINAL_CAPTION_XPATH)
        if caption_input is None:
            editor_next = self._first(EDITOR_NEXT_XPATH)
            if editor_next is not None:
                editor_next.click()
            else:
                size = self.driver.get_window_size()
                self.driver.execute_script(
                    "mobile: clickGesture",
                    {
                        "x": round(int(size["width"]) * 0.74),
                        "y": round(int(size["height"]) * 0.95),
                    },
                )
            caption_input = self._first(FINAL_CAPTION_XPATH)
            if caption_input is None:
                caption_input = self._first(CAPTION_XPATH)
        if caption_input is None:
            raise RuntimeError("caption input is not visible")
        caption_input.clear()
        caption_input.send_keys(caption.strip())

    def submit_once(self) -> None:
        if self._submitted:
            raise RuntimeError("publishing submission was already attempted")
        if self._verification_visible():
            raise VerificationRequired("TikTok verification challenge is visible")
        post = self._first(FINAL_POST_XPATH)
        if post is None:
            post = self._first(POST_XPATH)
        if post is None:
            raise RuntimeError("Post control is not visible")
        self._submitted = True
        post.click()
        self.sleeper(0.5)
        confirmation = self._first_now(PUBLISH_CONFIRM_XPATH)
        if confirmation is not None:
            confirmation.click()
        self.sleeper(self.post_submit_grace)

    def reconcile(self, before: frozenset[str]) -> str | None:
        if not self._submitted:
            raise RuntimeError("publishing submission was not attempted")
        self.driver.execute_script(
            "mobile: deepLink",
            {
                "url": f"https://www.tiktok.com/@{self.expected_username}",
                "package": TIKTOK_PACKAGE,
            },
        )
        deadline = time.monotonic() + self.timeout
        while True:
            self._dismiss_profile_modal()
            source = str(self.driver.page_source)
            if parse_profile_username(source) == self.expected_username:
                current = self._visible_post_keys(source) | self._tile_keys()
                added = set(current) - set(before)
                if added:
                    signature = hashlib.sha256(
                        "\n".join(sorted(added)).encode()
                    ).hexdigest()[:20]
                    return f"tiktok-visible://@{self.expected_username}/{signature}"
            if time.monotonic() >= deadline:
                return None
            self.sleeper(self.poll_interval)

    @staticmethod
    def _visible_post_keys(source: str) -> frozenset[str]:
        return frozenset(
            f"{index}:{value}"
            for index, value in enumerate(parse_visible_post_keys(source))
        )

    def _tile_keys(self) -> frozenset[str]:
        keys = set()
        for tile in self.driver.find_elements(By.XPATH, PROFILE_TILE_XPATH):
            try:
                if tile.is_displayed():
                    content = bytes(tile.screenshot_as_png)
                    if content:
                        keys.add(f"tile:{hashlib.sha256(content).hexdigest()}")
            except Exception:
                continue
        return frozenset(keys)

    def close(self) -> None:
        self.driver.quit()

    def _wait_profile_username(self) -> str:
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                actual = parse_profile_username(str(self.driver.page_source))
            except ValueError:
                actual = ""
            if actual or time.monotonic() >= deadline:
                return actual
            self.sleeper(self.poll_interval)

    def _wait_elements(self, xpath: str) -> tuple[object, ...]:
        deadline = time.monotonic() + self.timeout
        while True:
            visible = []
            for element in self.driver.find_elements(By.XPATH, xpath):
                try:
                    if element.is_displayed():
                        visible.append(element)
                except Exception:
                    continue
            if visible or time.monotonic() >= deadline:
                return tuple(visible)
            self.sleeper(self.poll_interval)

    def _first(self, xpath: str) -> object | None:
        elements = self._wait_elements(xpath)
        return elements[0] if elements else None

    def _first_now(self, xpath: str) -> object | None:
        for element in self.driver.find_elements(By.XPATH, xpath):
            try:
                if element.is_displayed():
                    return element
            except Exception:
                continue
        return None

    def _click_required(self, xpath: str, label: str) -> None:
        element = self._first(xpath)
        if element is None:
            raise RuntimeError(f"{label} is not visible")
        element.click()

    def _select_gallery_tile(self, index: int) -> None:
        xpath = f"({THUMBNAIL_TILE_XPATH})[{index + 1}]"
        for _ in range(10):
            elements = self.driver.find_elements(By.XPATH, xpath)
            if elements:
                try:
                    if elements[0].is_displayed():
                        elements[0].click()
                        return
                except WebDriverException:
                    pass
            size = self.driver.get_window_size()
            self.driver.execute_script(
                "mobile: swipeGesture",
                {
                    "left": 0,
                    "top": round(int(size["height"]) * 0.2),
                    "width": int(size["width"]),
                    "height": round(int(size["height"]) * 0.65),
                    "direction": "up",
                    "percent": 0.35,
                },
            )
            self.sleeper(0.25)
        raise RuntimeError(f"gallery image {index + 1} is not visible")

    def _verification_visible(self) -> bool:
        source = str(self.driver.page_source).lower()
        return any(marker in source for marker in VERIFICATION_MARKERS)

    def _allow_permissions(self) -> None:
        for _ in range(3):
            permission = self._first_now(PERMISSION_XPATH)
            if permission is None:
                return
            permission.click()

    def _dismiss_onboarding(self) -> None:
        source = str(self.driver.page_source).lower()
        if "start recording" not in source and "ai self" not in source:
            return
        close = self._first_now('//*[@content-desc="Close" or @text="Close"]')
        if close is not None:
            close.click()

    def _dismiss_profile_modal(self) -> None:
        contact_deny = self._first_now(
            '//*[@text="Don\'t allow" or @content-desc="Don\'t allow" '
            'or @text="不允许" or @content-desc="不允许"]'
        )
        if contact_deny is not None:
            contact_deny.click()
        close = self._first_now(
            f'//*[@resource-id="{TIKTOK_PACKAGE}:id/e2c" or @content-desc="Close"]'
        )
        if close is not None:
            close.click()
        dismiss = self._first_now(
            '//*[@text="Not now" or @content-desc="Not now" '
            'or @text="暂时不要" or @content-desc="暂时不要"]'
        )
        if dismiss is not None:
            dismiss.click()


def start_publish_activity(
    adb_endpoint: str,
    *,
    adb_path: Path | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    executable = adb_path or Path.home() / "Library/Android/sdk/platform-tools/adb"
    common = (str(executable), "-s", adb_endpoint, "shell", "am")
    runner(
        (*common, "force-stop", TIKTOK_PACKAGE),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    runner(
        (
            *common,
            "start",
            "-n",
            PUBLISH_ACTIVITY,
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


class PhotoPublishingUi(Protocol):
    def verify_identity(self, expected_username: str) -> None: ...

    def snapshot_posts(self) -> frozenset[str]: ...

    def prepare(self, remote_paths: tuple[str, ...], caption: str) -> None: ...

    def submit_once(self) -> None: ...

    def reconcile(self, before: frozenset[str]) -> str | None: ...

    def close(self) -> None: ...


class MediaStager(Protocol):
    def stage(self, job: PublishingJob) -> tuple[str, ...]: ...

    def cleanup(self, job_id: int) -> None: ...


class AdbMediaStager:
    def __init__(
        self,
        adb_endpoint: str,
        *,
        adb_path: Path | None = None,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self.adb_endpoint = adb_endpoint.strip()
        self.adb_path = (
            adb_path or Path.home() / "Library/Android/sdk/platform-tools/adb"
        )
        self.runner = runner

    def stage(self, job: PublishingJob) -> tuple[str, ...]:
        if len(job.asset_paths) != len(job.asset_sha256s):
            raise ValueError("publishing asset snapshot is incomplete")
        local_paths = tuple(Path(value) for value in job.asset_paths)
        for path, expected_hash in zip(local_paths, job.asset_sha256s, strict=True):
            if not path.is_file():
                raise ValueError(f"publishing asset is missing: {path}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"publishing asset SHA-256 mismatch: {path}")
        remote_dir = self._remote_dir(job.job_id)
        self._run("shell", "mkdir", "-p", remote_dir)
        remote_paths: list[str] = []
        for index, path in enumerate(local_paths, start=1):
            suffix = (
                path.suffix.lower()
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                else ".jpg"
            )
            remote_path = f"{remote_dir}/{index:03d}{suffix}"
            self._run("push", str(path), remote_path)
            self._run(
                "shell",
                "am",
                "broadcast",
                "-a",
                "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                "-d",
                f"file://{remote_path}",
            )
            remote_paths.append(remote_path)
        return tuple(remote_paths)

    def cleanup(self, job_id: int) -> None:
        self._run("shell", "rm", "-rf", self._remote_dir(job_id))

    def _remote_dir(self, job_id: int) -> str:
        if job_id <= 0:
            raise ValueError("job id must be positive")
        return f"/sdcard/Pictures/TikPoc/job-{job_id}"

    def _run(self, *args: str) -> object:
        return self.runner(
            (str(self.adb_path), "-s", self.adb_endpoint, *args),
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )


class MobileCatalogPublisher:
    def __init__(
        self,
        repository: PublishingRepository,
        *,
        stager: MediaStager,
        ui_factory: Callable[[], PhotoPublishingUi],
        device_busy: Callable[[str, int], bool] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.repository = repository
        self.stager = stager
        self.ui_factory = ui_factory
        self.device_busy = device_busy or self._device_busy
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def publish_one(
        self,
        *,
        account_id: str,
        expected_username: str,
        device_id: str,
        owner: str,
    ) -> PublishingJob | None:
        now_ms = self.clock_ms()
        if self.device_busy(device_id, now_ms):
            raise RuntimeError("device has an active acquisition worker")
        job = self.repository.claim_job(
            account_id=account_id,
            owner=owner,
            now_ms=now_ms,
            lease_ms=10 * 60 * 1000,
        )
        if job is None:
            return None
        ui: PhotoPublishingUi | None = None
        submitted = False
        try:
            remote_paths = self.stager.stage(job)
            ui = self.ui_factory()
            ui.verify_identity(expected_username)
            self._attempt(job, owner, "identity_verified")
            before = ui.snapshot_posts()
            ui.prepare(remote_paths, job.caption)
            self._attempt(job, owner, "media_prepared")
            try:
                ui.submit_once()
            except VerificationRequired:
                raise
            except Exception:
                submitted = True
                raise
            submitted = True
            self._attempt(job, owner, "submitted")
            visible_result = ui.reconcile(before)
            if visible_result:
                self._attempt(job, owner, "reconciled", visible_result)
                return self.repository.finish_job(
                    job.job_id,
                    owner=owner,
                    result="published",
                    visible_post_url=visible_result,
                    now_ms=self.clock_ms(),
                )
            return self.repository.finish_job(
                job.job_id,
                owner=owner,
                result="uncertain",
                visible_post_url="",
                now_ms=self.clock_ms(),
            )
        except Exception:
            if submitted:
                return self.repository.finish_job(
                    job.job_id,
                    owner=owner,
                    result="uncertain",
                    visible_post_url="",
                    now_ms=self.clock_ms(),
                )
            self.repository.release_job(job.job_id, owner=owner, now_ms=self.clock_ms())
            raise
        finally:
            if ui is not None:
                try:
                    ui.close()
                except Exception:
                    pass
            try:
                self.stager.cleanup(job.job_id)
            except Exception:
                pass

    def _attempt(
        self, job: PublishingJob, owner: str, stage: str, detail: str = ""
    ) -> None:
        self.repository.record_attempt(
            job.job_id,
            owner=owner,
            stage=stage,
            detail=detail,
            now_ms=self.clock_ms(),
        )

    def _device_busy(self, device_id: str, now_ms: int) -> bool:
        try:
            with sqlite3.connect(self.repository.path) as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM device_worker_leases
                    WHERE device_id = ? AND expires_at_ms > ? LIMIT 1
                    """,
                    (device_id, now_ms),
                ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None

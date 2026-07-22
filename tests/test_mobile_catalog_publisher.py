from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tikpoc.catalog import CatalogProduct
from tikpoc.mobile_catalog_publisher import (
    AdbMediaStager,
    AppiumTikTokPhotoUi,
    IdentityMismatch,
    MobileCatalogPublisher,
    VerificationRequired,
)
from tikpoc.publishing_db import PublishingRepository


def _product() -> CatalogProduct:
    return CatalogProduct(
        source_key="gxhy:shop-01:product-01",
        source_id="product-01",
        shop_id="shop-01",
        title="Bag",
        description="Bag",
        created_time=1,
        image_urls=("https://example/one.jpg", "https://example/two.jpg"),
    )


def _approved_job(repository: PublishingRepository, tmp_path: Path):
    paths = []
    hashes = []
    for index, content in enumerate((b"front", b"detail"), start=1):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(content)
        paths.append(path)
        hashes.append(hashlib.sha256(content).hexdigest())
    job = repository.prepare_job(
        _product(),
        account_id="account-01",
        caption="A concise bag caption.",
        asset_paths=tuple(paths),
        asset_sha256s=tuple(hashes),
        now_ms=100,
    )
    return repository.approve_job(job.job_id, now_ms=110)


def test_adb_stager_pushes_all_job_images_to_isolated_album(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)
    commands: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        commands.append(tuple(command))
        return type("Result", (), {"stdout": ""})()

    remote = AdbMediaStager("device-01", runner=runner).stage(job)

    assert remote == (
        "/sdcard/Pictures/TikPoc/job-1/001.jpg",
        "/sdcard/Pictures/TikPoc/job-1/002.jpg",
    )
    assert any(
        command[-4:] == ("shell", "mkdir", "-p", "/sdcard/Pictures/TikPoc/job-1")
        for command in commands
    )
    assert sum("push" in command for command in commands) == 2
    assert (
        sum(
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE" in command
            for command in commands
        )
        == 2
    )
    assert all("job-1" in " ".join(command) for command in commands)


def test_adb_stager_rejects_changed_asset_before_device_write(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)
    Path(job.asset_paths[0]).write_bytes(b"changed")
    commands: list[object] = []

    with pytest.raises(ValueError, match="SHA-256"):
        AdbMediaStager(
            "device-01", runner=lambda *args, **kwargs: commands.append(args)
        ).stage(job)

    assert commands == []


class FakeStager:
    def __init__(self) -> None:
        self.cleaned: list[int] = []

    def stage(self, job):
        return tuple(f"/remote/{index}.jpg" for index, _ in enumerate(job.asset_paths))

    def cleanup(self, job_id: int) -> None:
        self.cleaned.append(job_id)


class FakeUi:
    def __init__(self, *, mismatch: bool = False, reconcile: str | None = "post-123"):
        self.mismatch = mismatch
        self.reconcile_result = reconcile
        self.prepared: tuple[tuple[str, ...], str] | None = None
        self.submit_calls = 0
        self.closed = False

    def verify_identity(self, expected_username: str) -> None:
        if self.mismatch:
            raise IdentityMismatch(f"expected {expected_username}")

    def snapshot_posts(self) -> frozenset[str]:
        return frozenset({"old"})

    def prepare(self, remote_paths: tuple[str, ...], caption: str) -> None:
        self.prepared = (remote_paths, caption)

    def submit_once(self) -> None:
        self.submit_calls += 1

    def reconcile(self, _before: frozenset[str]) -> str | None:
        return self.reconcile_result

    def close(self) -> None:
        self.closed = True


def test_mobile_publisher_uploads_one_multi_image_job_and_reconciles(
    tmp_path: Path,
) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)
    stager = FakeStager()
    ui = FakeUi()
    publisher = MobileCatalogPublisher(
        repository,
        stager=stager,
        ui_factory=lambda: ui,
        device_busy=lambda _device_id, _now_ms: False,
        clock_ms=lambda: 500,
    )

    result = publisher.publish_one(
        account_id="account-01",
        expected_username="user8362279234711",
        device_id="myt-slot-01",
        owner="publisher-01",
    )

    assert result is not None
    assert result.state == "published"
    assert result.visible_post_url == "post-123"
    assert ui.prepared == (("/remote/0.jpg", "/remote/1.jpg"), job.caption)
    assert ui.submit_calls == 1
    assert ui.closed is True
    assert stager.cleaned == [job.job_id]
    assert [attempt.stage for attempt in repository.attempts(job.job_id)] == [
        "identity_verified",
        "media_prepared",
        "submitted",
        "reconciled",
    ]


def test_identity_mismatch_releases_job_without_submit(tmp_path: Path) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)
    ui = FakeUi(mismatch=True)
    publisher = MobileCatalogPublisher(
        repository,
        stager=FakeStager(),
        ui_factory=lambda: ui,
        device_busy=lambda *_: False,
        clock_ms=lambda: 500,
    )

    with pytest.raises(IdentityMismatch):
        publisher.publish_one(
            account_id="account-01",
            expected_username="expected",
            device_id="myt-slot-01",
            owner="publisher-01",
        )

    assert repository.get_job(job.job_id).state == "approved"
    assert ui.submit_calls == 0


def test_missing_visible_result_becomes_uncertain_and_is_not_retried(
    tmp_path: Path,
) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    _approved_job(repository, tmp_path)
    ui = FakeUi(reconcile=None)
    publisher = MobileCatalogPublisher(
        repository,
        stager=FakeStager(),
        ui_factory=lambda: ui,
        device_busy=lambda *_: False,
        clock_ms=lambda: 500,
    )

    result = publisher.publish_one(
        account_id="account-01",
        expected_username="expected",
        device_id="myt-slot-01",
        owner="publisher-01",
    )

    assert result is not None and result.state == "uncertain"
    assert ui.submit_calls == 1
    assert (
        publisher.publish_one(
            account_id="account-01",
            expected_username="expected",
            device_id="myt-slot-01",
            owner="publisher-02",
        )
        is None
    )


def test_verification_before_submit_releases_job_for_human_resolution(
    tmp_path: Path,
) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)

    class ChallengeUi(FakeUi):
        def submit_once(self) -> None:
            raise VerificationRequired("challenge")

    publisher = MobileCatalogPublisher(
        repository,
        stager=FakeStager(),
        ui_factory=ChallengeUi,
        device_busy=lambda *_: False,
        clock_ms=lambda: 500,
    )

    with pytest.raises(VerificationRequired):
        publisher.publish_one(
            account_id="account-01",
            expected_username="expected",
            device_id="myt-slot-01",
            owner="publisher-01",
        )

    assert repository.get_job(job.job_id).state == "approved"
    assert [attempt.stage for attempt in repository.attempts(job.job_id)] == [
        "identity_verified",
        "media_prepared",
    ]


def test_active_acquisition_worker_blocks_publisher_before_claim(
    tmp_path: Path,
) -> None:
    repository = PublishingRepository(tmp_path / "publishing.db")
    job = _approved_job(repository, tmp_path)
    publisher = MobileCatalogPublisher(
        repository,
        stager=FakeStager(),
        ui_factory=lambda: FakeUi(),
        device_busy=lambda *_: True,
        clock_ms=lambda: 500,
    )

    with pytest.raises(RuntimeError, match="acquisition worker"):
        publisher.publish_one(
            account_id="account-01",
            expected_username="expected",
            device_id="myt-slot-01",
            owner="publisher-01",
        )

    assert repository.get_job(job.job_id).state == "approved"


class AppiumElement:
    def __init__(self, text: str = "", *, on_click=None) -> None:
        self.text = text
        self.on_click = on_click
        self.clicks = 0
        self.value = ""

    def click(self) -> None:
        self.clicks += 1
        if self.on_click:
            self.on_click()

    def send_keys(self, value: str) -> None:
        self.value = value

    def clear(self) -> None:
        self.value = ""

    def is_displayed(self) -> bool:
        return True


class AppiumPublishingDriver:
    def __init__(self, *, username: str = "expected", captcha: bool = False) -> None:
        self.username = username
        self.captcha = captcha
        self.created = AppiumElement()
        self.upload = AppiumElement()
        self.photos = AppiumElement()
        self.album = AppiumElement()
        self.job_album = AppiumElement()
        self.multi = AppiumElement()
        self.thumbnails = [AppiumElement(), AppiumElement(), AppiumElement()]
        self.next = AppiumElement()
        self.caption = AppiumElement()
        self.post = AppiumElement(on_click=self._publish)
        self.published = False
        self.scripts = []
        self.quit_calls = 0

    @property
    def page_source(self) -> str:
        challenge = '<node text="Verify to continue" />' if self.captcha else ""
        key = "new-post" if self.published else "old-post"
        return (
            '<hierarchy><node resource-id="com.zhiliaoapp.musically:id/s7e" '
            f'text="@{self.username}"/><node resource-id="x:id/tv_play_count" '
            f'text="{key}"/>{challenge}</hierarchy>'
        )

    def execute_script(self, name, args) -> None:
        self.scripts.append((name, args))

    def find_elements(self, _by, xpath):
        if "Create" in xpath or "创建" in xpath:
            return [self.created]
        if "Upload" in xpath or "上传" in xpath:
            return [self.upload]
        if "Photos" in xpath or "照片" in xpath:
            return [self.photos]
        if "job-1" in xpath:
            return [self.job_album]
        if "All" in xpath or "Recents" in xpath or "相册" in xpath:
            return [self.album]
        if "Select multiple" in xpath or "选择多个" in xpath:
            return [self.multi]
        if "icon_check" in xpath:
            return self.thumbnails
        if "Next" in xpath or "下一步" in xpath:
            return [self.next]
        if "EditText" in xpath:
            return [self.caption]
        if "Post" in xpath or "发布" in xpath:
            return [self.post]
        return []

    def quit(self) -> None:
        self.quit_calls += 1

    def _publish(self) -> None:
        self.published = True


def test_appium_photo_ui_selects_exact_job_album_images_and_reconciles() -> None:
    driver = AppiumPublishingDriver()
    ui = AppiumTikTokPhotoUi(driver, timeout=0, sleeper=lambda _: None)

    ui.verify_identity("@expected")
    before = ui.snapshot_posts()
    ui.prepare(
        (
            "/sdcard/Pictures/TikPoc/job-1/001.jpg",
            "/sdcard/Pictures/TikPoc/job-1/002.jpg",
        ),
        "One product, two views.",
    )
    ui.submit_once()

    assert driver.job_album.clicks == 1
    assert [element.clicks for element in driver.thumbnails] == [1, 1, 0]
    assert driver.caption.value == "One product, two views."
    assert driver.post.clicks == 1
    assert ui.reconcile(before).startswith("tiktok-visible://@expected/")


def test_appium_photo_ui_rejects_identity_mismatch_before_create() -> None:
    driver = AppiumPublishingDriver(username="other")
    ui = AppiumTikTokPhotoUi(driver, timeout=0, sleeper=lambda _: None)

    with pytest.raises(IdentityMismatch):
        ui.verify_identity("expected")

    assert driver.created.clicks == 0


def test_appium_photo_ui_blocks_captcha_before_single_submit() -> None:
    driver = AppiumPublishingDriver(captcha=True)
    ui = AppiumTikTokPhotoUi(driver, timeout=0, sleeper=lambda _: None)
    ui.verify_identity("expected")
    ui.prepare(("/sdcard/Pictures/TikPoc/job-1/001.jpg",), "Caption")

    with pytest.raises(VerificationRequired):
        ui.submit_once()

    assert driver.post.clicks == 0

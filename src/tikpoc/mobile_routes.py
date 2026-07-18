import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path


class AdbRouteError(RuntimeError):
    pass


class AdbProfileRouter:
    def __init__(
        self,
        adb_endpoint: str,
        *,
        adb_path: Path | None = None,
        timeout_seconds: float = 10,
        runner: Callable[..., object] = subprocess.run,
    ) -> None:
        self.adb_endpoint = str(adb_endpoint).strip()
        self.adb_path = adb_path or Path(
            os.environ.get(
                "ANDROID_ADB",
                Path.home() / "Library/Android/sdk/platform-tools/adb",
            )
        )
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def open(self, uri: str) -> None:
        normalized_uri = str(uri).strip()
        if not self.adb_endpoint or not normalized_uri:
            raise ValueError("ADB route endpoint and URI are required")
        command: Sequence[str] = (
            str(self.adb_path),
            "-s",
            self.adb_endpoint,
            "shell",
            "am",
            "start",
            "-W",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            normalized_uri,
            "com.zhiliaoapp.musically",
        )
        try:
            self.runner(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            raise AdbRouteError("TikTok ADB route failed") from None

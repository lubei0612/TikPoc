import os
import socket
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .fleet import FleetConfig, FleetDevice


@dataclass(frozen=True)
class ProxyHealth:
    device_id: str
    adb_state: str
    proxy_state: str
    http_status: int | None


def _source_address(destination: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect((destination, 9))
        return str(probe.getsockname()[0])


def _listener_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class ProxyGuard:
    def __init__(
        self,
        config: FleetConfig,
        *,
        adb_path: Path | None = None,
        source_address: Callable[[str], str] = _source_address,
        listener_probe: Callable[[str, int], bool] = _listener_probe,
        runner: Callable[..., object] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
        recovery_wait_seconds: float = 5.0,
        timeout_seconds: float = 12.0,
    ) -> None:
        self.config = config
        self.adb_path = adb_path or Path(
            os.environ.get(
                "ANDROID_ADB",
                Path.home() / "Library/Android/sdk/platform-tools/adb",
            )
        )
        self.source_address = source_address
        self.listener_probe = listener_probe
        self.runner = runner
        self.sleeper = sleeper
        self.recovery_wait_seconds = max(0.0, float(recovery_wait_seconds))
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    def reconcile(self) -> tuple[ProxyHealth, ...]:
        try:
            proxy_host = self.source_address(self.config.myt_host)
        except (OSError, ValueError):
            return tuple(
                ProxyHealth(device.device_id, "unknown", "source_unavailable", None)
                for device in self.config.devices
            )
        proxy_port = self.config.relay_upstream_port
        listener_ready = self.listener_probe("127.0.0.1", proxy_port)
        if not listener_ready:
            try:
                self._run(("open", "-gja", "-a", "Clash Verge"))
                self.sleeper(self.recovery_wait_seconds)
            except (OSError, subprocess.SubprocessError):
                pass
            listener_ready = self.listener_probe("127.0.0.1", proxy_port)
        if not listener_ready:
            return tuple(
                ProxyHealth(device.device_id, "unknown", "listener_unavailable", None)
                for device in self.config.devices
            )
        return tuple(
            self._reconcile_device(device, proxy_host, proxy_port)
            for device in self.config.devices
        )

    def _reconcile_device(
        self, device: FleetDevice, proxy_host: str, proxy_port: int
    ) -> ProxyHealth:
        try:
            self._run((str(self.adb_path), "connect", device.adb_endpoint))
            adb_state = self._run(
                (str(self.adb_path), "-s", device.adb_endpoint, "get-state")
            )
            if adb_state != "device":
                return ProxyHealth(
                    device.device_id, adb_state or "offline", "device_unavailable", None
                )
            observed = (
                self._setting(device, "http_proxy"),
                self._setting(device, "global_http_proxy_host"),
                self._setting(device, "global_http_proxy_port"),
            )
            expected = (f"{proxy_host}:{proxy_port}", proxy_host, str(proxy_port))
            proxy_state = "healthy"
            if observed != expected:
                for field, value in zip(
                    (
                        "http_proxy",
                        "global_http_proxy_host",
                        "global_http_proxy_port",
                    ),
                    expected,
                    strict=True,
                ):
                    self._run(
                        (
                            str(self.adb_path),
                            "-s",
                            device.adb_endpoint,
                            "shell",
                            "settings",
                            "put",
                            "global",
                            field,
                            value,
                        )
                    )
                proxy_state = "corrected"
            return ProxyHealth(
                device.device_id,
                adb_state,
                proxy_state,
                self._http_probe(device, proxy_host, proxy_port),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return ProxyHealth(device.device_id, "offline", "device_unavailable", None)

    def _setting(self, device: FleetDevice, field: str) -> str:
        return self._run(
            (
                str(self.adb_path),
                "-s",
                device.adb_endpoint,
                "shell",
                "settings",
                "get",
                "global",
                field,
            )
        )

    def _http_probe(
        self, device: FleetDevice, proxy_host: str, proxy_port: int
    ) -> int | None:
        try:
            output = self._run(
                (
                    str(self.adb_path),
                    "-s",
                    device.adb_endpoint,
                    "shell",
                    "curl",
                    "-sS",
                    "-x",
                    f"http://{proxy_host}:{proxy_port}",
                    "--connect-timeout",
                    "5",
                    "--max-time",
                    "12",
                    "-o",
                    "/dev/null",
                    "-w",
                    "%{http_code}",
                    "https://www.tiktok.com/",
                )
            )
            return int(output) if output.isdigit() else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def _run(self, command: Sequence[str]) -> str:
        completed = self.runner(
            tuple(command),
            check=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
        )
        return str(getattr(completed, "stdout", "")).strip()

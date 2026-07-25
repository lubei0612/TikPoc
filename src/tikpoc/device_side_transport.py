import json
import socket
import struct
import subprocess
import threading
from collections.abc import Callable, Mapping
from typing import Any

MAX_PAYLOAD_BYTES = 262_144
_CLAIMS: set[tuple[str, int]] = set()
_CLAIMS_LOCK = threading.Lock()


class DeviceSideTransportError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


Runner = Callable[..., subprocess.CompletedProcess[str]]


class DeviceSideTransport:
    def __init__(
        self,
        adb_endpoint: str,
        *,
        host_port: int,
        device_port: int,
        timeout_seconds: float = 10,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        runner: Runner = subprocess.run,
    ) -> None:
        if (
            not adb_endpoint.strip()
            or not 1 <= host_port <= 65_535
            or not 1 <= device_port <= 65_535
            or timeout_seconds <= 0
            or not 1 <= max_payload_bytes <= MAX_PAYLOAD_BYTES
        ):
            raise ValueError("invalid device-side transport configuration")
        self.adb_endpoint = adb_endpoint.strip()
        self.host_port = host_port
        self.device_port = device_port
        self.timeout_seconds = timeout_seconds
        self.max_payload_bytes = max_payload_bytes
        self._runner = runner
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        claim = (self.adb_endpoint, self.host_port)
        with _CLAIMS_LOCK:
            if claim in _CLAIMS:
                raise DeviceSideTransportError("forward_already_claimed")
            _CLAIMS.add(claim)
        try:
            self._run_adb("forward", f"tcp:{self.host_port}", f"tcp:{self.device_port}")
        except (OSError, subprocess.SubprocessError):
            with _CLAIMS_LOCK:
                _CLAIMS.discard(claim)
            raise DeviceSideTransportError("forward_setup_failed") from None
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        claim = (self.adb_endpoint, self.host_port)
        try:
            self._run_adb("forward", "--remove", f"tcp:{self.host_port}")
        except (OSError, subprocess.SubprocessError):
            raise DeviceSideTransportError("forward_cleanup_failed") from None
        finally:
            self._started = False
            with _CLAIMS_LOCK:
                _CLAIMS.discard(claim)

    def request(self, payload: Mapping[str, object]) -> dict[str, object]:
        if not self._started:
            raise DeviceSideTransportError("transport_not_started")
        try:
            encoded = json.dumps(
                dict(payload), ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise DeviceSideTransportError("request_not_json") from None
        if not encoded or len(encoded) > self.max_payload_bytes:
            raise DeviceSideTransportError("request_too_large")
        try:
            with socket.create_connection(
                ("127.0.0.1", self.host_port), timeout=self.timeout_seconds
            ) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(struct.pack(">I", len(encoded)) + encoded)
                response_length = struct.unpack(">I", _receive_exact(connection, 4))[0]
                if not 1 <= response_length <= self.max_payload_bytes:
                    raise DeviceSideTransportError("response_too_large")
                response_bytes = _receive_exact(connection, response_length)
        except DeviceSideTransportError:
            raise
        except TimeoutError:
            raise DeviceSideTransportError("transport_timeout") from None
        except (OSError, EOFError, struct.error):
            raise DeviceSideTransportError("transport_lost") from None
        try:
            decoded: Any = json.loads(response_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DeviceSideTransportError("invalid_response_json") from None
        if not isinstance(decoded, dict):
            raise DeviceSideTransportError("response_not_object")
        return decoded

    def _run_adb(self, *arguments: str) -> None:
        self._runner(
            ["adb", "-s", self.adb_endpoint, *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )


def _receive_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise EOFError
        result.extend(chunk)
    return bytes(result)

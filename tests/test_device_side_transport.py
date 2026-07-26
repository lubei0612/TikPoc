import json
import os
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tikpoc.device_side_transport import (
    DeviceSideTransport,
    DeviceSideTransportError,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: object) -> CompletedProcess[str]:
        self.commands.append(command)
        assert kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
        return CompletedProcess(command, 0, "", "")


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def serve_once(port: int, responder: Callable[[bytes], bytes]) -> threading.Thread:
    ready = threading.Event()

    def serve() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen(1)
            ready.set()
            connection, _ = listener.accept()
            with connection:
                length = struct.unpack(">I", receive_exact(connection, 4))[0]
                request = receive_exact(connection, length)
                response = responder(request)
                connection.sendall(struct.pack(">I", len(response)) + response)

    thread = threading.Thread(target=serve)
    thread.start()
    assert ready.wait(1)
    return thread


def receive_exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise EOFError
        result.extend(chunk)
    return bytes(result)


def test_start_and_close_own_serial_scoped_forward() -> None:
    runner = RecordingRunner()
    transport = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=available_port(), device_port=47101, runner=runner
    )

    transport.start()
    transport.close()

    assert runner.commands == [
        [
            "adb",
            "-s",
            "ADB_ENDPOINT",
            "forward",
            f"tcp:{transport.host_port}",
            "tcp:47101",
        ],
        [
            "adb",
            "-s",
            "ADB_ENDPOINT",
            "forward",
            "--remove",
            f"tcp:{transport.host_port}",
        ],
    ]


def test_request_exchanges_bounded_json_frame() -> None:
    runner = RecordingRunner()
    port = available_port()
    server = serve_once(
        port,
        lambda request: json.dumps(
            {"status": "ok", "received": json.loads(request)}
        ).encode(),
    )
    transport = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    transport.start()
    try:
        assert transport.request({"command_id": "cmd-1"}) == {
            "status": "ok",
            "received": {"command_id": "cmd-1"},
        }
    finally:
        transport.close()
        server.join(1)


def test_transport_lost_replays_same_command_once() -> None:
    runner = RecordingRunner()
    port = available_port()
    ready = threading.Event()
    received: list[bytes] = []

    def serve() -> None:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
            listener.listen(2)
            ready.set()
            for attempt in range(2):
                connection, _ = listener.accept()
                with connection:
                    length = struct.unpack(">I", receive_exact(connection, 4))[0]
                    received.append(receive_exact(connection, length))
                    if attempt == 1:
                        response = b'{"status":"ok"}'
                        connection.sendall(struct.pack(">I", len(response)) + response)

    server = threading.Thread(target=serve)
    server.start()
    assert ready.wait(1)
    transport = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    transport.start()
    try:
        assert transport.request({"command_id": "stable-command"}) == {"status": "ok"}
    finally:
        transport.close()
        server.join(1)

    assert received[0] == received[1]


def test_oversized_response_is_rejected_without_payload_in_error() -> None:
    runner = RecordingRunner()
    port = available_port()
    server = serve_once(port, lambda _: b"x" * 262_145)
    transport = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    transport.start()
    try:
        with pytest.raises(
            DeviceSideTransportError, match="response_too_large"
        ) as raised:
            transport.request({"secret": "DO_NOT_PRINT"})
        assert "DO_NOT_PRINT" not in str(raised.value)
    finally:
        transport.close()
        server.join(1)


def test_same_adb_and_host_port_cannot_be_claimed_twice() -> None:
    runner = RecordingRunner()
    port = available_port()
    first = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    second = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    first.start()
    try:
        with pytest.raises(DeviceSideTransportError, match="forward_already_claimed"):
            second.start()
    finally:
        first.close()


def test_same_forward_is_locked_across_processes(tmp_path) -> None:
    runner = RecordingRunner()
    port = available_port()
    first = DeviceSideTransport(
        "ADB_ENDPOINT",
        host_port=port,
        device_port=47101,
        lock_dir=tmp_path,
        runner=runner,
    )
    first.start()
    try:
        script = """
import sys
from subprocess import CompletedProcess
from tikpoc.device_side_transport import DeviceSideTransport, DeviceSideTransportError

transport = DeviceSideTransport(
    'ADB_ENDPOINT', host_port=int(sys.argv[1]), device_port=47101,
    lock_dir=sys.argv[2], runner=lambda command, **kwargs: CompletedProcess(command, 0, '', ''),
)
try:
    transport.start()
except DeviceSideTransportError as error:
    print(error.code)
else:
    print('acquired')
    transport.close()
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(port), os.fspath(tmp_path)],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.fspath(Path(__file__).parents[1] / "src"),
            },
        )
        assert result.stdout.strip() == "forward_already_claimed"
    finally:
        first.close()


def test_failed_forward_releases_process_claim() -> None:
    port = available_port()

    def failing_runner(*args: object, **kwargs: object) -> CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, "adb")

    failed = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=failing_runner
    )
    with pytest.raises(DeviceSideTransportError, match="forward_setup_failed"):
        failed.start()

    runner = RecordingRunner()
    replacement = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=port, device_port=47101, runner=runner
    )
    replacement.start()
    replacement.close()


def test_missing_path_adb_retries_with_android_home(tmp_path, monkeypatch) -> None:
    adb = tmp_path / "platform-tools" / "adb"
    adb.parent.mkdir(parents=True)
    adb.write_bytes(b"")
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        commands.append(command)
        if command[0] == "adb":
            raise FileNotFoundError("adb")
        return CompletedProcess(command, 0, "", "")

    transport = DeviceSideTransport(
        "ADB_ENDPOINT", host_port=available_port(), device_port=47101, runner=runner
    )
    transport.start()
    transport.close()

    assert commands[0][0] == "adb"
    assert commands[1][0] == str(adb)
    assert commands[2][0] == str(adb)

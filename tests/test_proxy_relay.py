import socket
import socketserver
import threading
import time

import pytest

from tikpoc.proxy_relay import ProxyRelay, RelayPolicy


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        payload = self.request.recv(4096)
        self.request.sendall(payload)


class EchoServer:
    def __init__(self) -> None:
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class SlowSinkHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        time.sleep(0.05)
        while payload := self.request.recv(65_536):
            self.server.received.extend(payload)


class SlowSinkServer(EchoServer):
    def __init__(self) -> None:
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SlowSinkHandler)
        self.server.received = bytearray()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def received(self) -> bytes:
        return bytes(self.server.received)


def test_relay_rejects_a_source_outside_allowlist() -> None:
    policy = RelayPolicy(allowed_sources=frozenset({"192.168.28.114"}))

    assert policy.permits("192.168.28.114") is True
    assert policy.permits("192.168.28.200") is False


def test_relay_forwards_bytes_to_loopback_upstream() -> None:
    payload = b"CONNECT example.test:443 HTTP/1.1\r\n\r\n"
    with EchoServer() as upstream:
        with ProxyRelay(
            "127.0.0.1",
            0,
            "127.0.0.1",
            upstream.port,
            allowed_sources={"127.0.0.1"},
        ) as relay:
            with socket.create_connection(
                ("127.0.0.1", relay.port), timeout=2
            ) as client:
                client.sendall(payload)
                received = client.recv(len(payload))

    assert received == payload
    assert relay.health()["accepted_connections"] == 1


def test_relay_requires_a_loopback_upstream() -> None:
    with pytest.raises(ValueError, match="loopback"):
        ProxyRelay(
            "127.0.0.1",
            0,
            "192.0.2.50",
            7897,
            allowed_sources={"127.0.0.1"},
        )


def test_relay_handler_rejects_disallowed_socket_source() -> None:
    with EchoServer() as upstream:
        with ProxyRelay(
            "127.0.0.1",
            0,
            "127.0.0.1",
            upstream.port,
            allowed_sources={"192.0.2.10"},
        ) as relay:
            with socket.create_connection(
                ("127.0.0.1", relay.port), timeout=2
            ) as client:
                client.sendall(b"blocked")
                try:
                    received = client.recv(1)
                except ConnectionResetError:
                    received = b""
                assert received == b""

    assert relay.health()["rejected_connections"] == 1
    assert relay.health()["accepted_connections"] == 0


def test_relay_preserves_large_half_closed_stream_under_backpressure() -> None:
    payload = b"x" * (4 * 1024 * 1024)
    with SlowSinkServer() as upstream:
        with ProxyRelay(
            "127.0.0.1",
            0,
            "127.0.0.1",
            upstream.port,
            allowed_sources={"127.0.0.1"},
            idle_timeout=5,
        ) as relay:
            with socket.create_connection(
                ("127.0.0.1", relay.port), timeout=5
            ) as client:
                client.sendall(payload)
                client.shutdown(socket.SHUT_WR)
                while client.recv(65_536):
                    pass

    assert upstream.received == payload

import ipaddress
import selectors
import socket
import socketserver
import threading
from dataclasses import dataclass


_COPY_CHUNK_BYTES = 65_536
_MAX_PENDING_BYTES = 262_144


@dataclass(frozen=True)
class RelayPolicy:
    allowed_sources: frozenset[str]

    def __post_init__(self) -> None:
        normalized = frozenset(
            str(ipaddress.ip_address(value)) for value in self.allowed_sources
        )
        object.__setattr__(self, "allowed_sources", normalized)

    def permits(self, source_ip: str) -> bool:
        try:
            normalized = str(ipaddress.ip_address(source_ip))
        except ValueError:
            return False
        return normalized in self.allowed_sources


class _RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _RelayHandler(socketserver.BaseRequestHandler):
    server: _RelayServer

    def handle(self) -> None:
        relay = self.server.relay
        source_ip = str(self.client_address[0])
        if not relay.policy.permits(source_ip):
            relay._increment("rejected_connections")
            return
        try:
            upstream = socket.create_connection(
                (relay.upstream_host, relay.upstream_port), timeout=relay.idle_timeout
            )
        except OSError:
            relay._increment("upstream_failures")
            return
        relay._increment("accepted_connections")
        with upstream:
            _copy_bidirectional(self.request, upstream, relay.idle_timeout)


def _copy_bidirectional(
    client: socket.socket, upstream: socket.socket, idle_timeout: float
) -> None:
    endpoints = (client, upstream)
    peer = {client: upstream, upstream: client}
    pending = {client: bytearray(), upstream: bytearray()}
    read_open = {client: True, upstream: True}
    write_open = {client: True, upstream: True}
    for endpoint in endpoints:
        endpoint.setblocking(False)

    with selectors.DefaultSelector() as selector:
        while True:
            for endpoint in endpoints:
                try:
                    selector.unregister(endpoint)
                except KeyError:
                    pass
                events = 0
                if (
                    read_open[endpoint]
                    and len(pending[peer[endpoint]]) < _MAX_PENDING_BYTES
                ):
                    events |= selectors.EVENT_READ
                if write_open[endpoint] and pending[endpoint]:
                    events |= selectors.EVENT_WRITE
                if events:
                    selector.register(endpoint, events)

            if not selector.get_map():
                return
            ready = selector.select(idle_timeout)
            if not ready:
                return
            for key, events in ready:
                endpoint = key.fileobj
                if events & selectors.EVENT_READ:
                    try:
                        payload = endpoint.recv(_COPY_CHUNK_BYTES)
                    except BlockingIOError:
                        payload = None
                    except OSError:
                        return
                    if payload:
                        pending[peer[endpoint]].extend(payload)
                    elif payload == b"":
                        read_open[endpoint] = False
                        target = peer[endpoint]
                        if not pending[target] and write_open[target]:
                            try:
                                target.shutdown(socket.SHUT_WR)
                            except OSError:
                                pass
                            write_open[target] = False

                if events & selectors.EVENT_WRITE and pending[endpoint]:
                    try:
                        sent = endpoint.send(pending[endpoint])
                    except BlockingIOError:
                        sent = 0
                    except OSError:
                        return
                    if sent:
                        del pending[endpoint][:sent]
                    source = peer[endpoint]
                    if (
                        not pending[endpoint]
                        and not read_open[source]
                        and write_open[endpoint]
                    ):
                        try:
                            endpoint.shutdown(socket.SHUT_WR)
                        except OSError:
                            pass
                        write_open[endpoint] = False

            if not any(read_open.values()) and not any(pending.values()):
                return


class ProxyRelay:
    def __init__(
        self,
        bind_host: str,
        bind_port: int,
        upstream_host: str,
        upstream_port: int,
        *,
        allowed_sources: set[str] | frozenset[str],
        idle_timeout: float = 30.0,
    ) -> None:
        self.bind_host = str(bind_host).strip()
        if self.bind_host in {"", "0.0.0.0", "::"}:
            raise ValueError("proxy relay must bind a specific host address")
        self.bind_port = int(bind_port)
        self.upstream_host = str(upstream_host).strip()
        self.upstream_port = int(upstream_port)
        try:
            upstream_address = ipaddress.ip_address(self.upstream_host)
        except ValueError as error:
            raise ValueError(
                "proxy relay upstream must be a loopback address"
            ) from error
        if not upstream_address.is_loopback:
            raise ValueError("proxy relay upstream must be a loopback address")
        self.idle_timeout = idle_timeout
        self.policy = RelayPolicy(frozenset(allowed_sources))
        self._server: _RelayServer | None = None
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self._lock = threading.Lock()
        self._counters = {
            "accepted_connections": 0,
            "rejected_connections": 0,
            "upstream_failures": 0,
        }

    @property
    def port(self) -> int:
        with self._lifecycle_lock:
            return (
                self.bind_port
                if self._server is None
                else int(self._server.server_address[1])
            )

    def start(self) -> "ProxyRelay":
        with self._lifecycle_lock:
            if self._server is not None:
                return self
            server = _RelayServer((self.bind_host, self.bind_port), _RelayHandler)
            server.relay = self
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        with self._lifecycle_lock:
            if self._server is None:
                return
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._server = None
            self._thread = None

    def health(self) -> dict[str, int | str | bool]:
        with self._lock:
            counters = dict(self._counters)
        with self._lifecycle_lock:
            running = self._server is not None
            port = self.port
        return {
            "running": running,
            "bind_host": self.bind_host,
            "port": port,
            **counters,
        }

    def _increment(self, name: str) -> None:
        with self._lock:
            self._counters[name] += 1

    def __enter__(self) -> "ProxyRelay":
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

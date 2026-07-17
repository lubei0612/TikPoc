import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


_HEALTH_FIELDS = frozenset(
    {
        "id",
        "name",
        "status",
        "indexNum",
        "index",
        "ip",
        "image",
        "androidType",
        "created",
        "started",
        "finished",
        "adbPort",
        "webPort",
        "portBindings",
    }
)


class MytSdkError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        sdk_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.sdk_code = sdk_code


class JsonTransport(Protocol):
    def request(self, method: str, url: str, json_body=None): ...


class UrllibJsonTransport:
    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def request(self, method: str, url: str, json_body=None):
        body = None if json_body is None else json.dumps(json_body).encode()
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body else {},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except HTTPError as error:
            raise MytSdkError(
                f"MYT SDK HTTP {error.code}", status_code=error.code
            ) from None
        except URLError:
            raise MytSdkError("MYT SDK request failed") from None
        try:
            return json.loads(payload)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise MytSdkError("MYT SDK returned invalid JSON") from None


@dataclass(frozen=True)
class MytSlot:
    slot_id: str
    name: str
    status: str
    slot_index: int
    ip: str
    adb_endpoint: str
    web_port: int
    image: str
    raw: dict[str, object]


class MytClient:
    def __init__(
        self,
        host: str,
        *,
        sdk_port: int = 8000,
        timeout: float = 10.0,
        transport: JsonTransport | None = None,
    ) -> None:
        self.host = str(host).strip()
        if not self.host:
            raise ValueError("MYT host is empty")
        if not 1 <= int(sdk_port) <= 65535:
            raise ValueError("MYT SDK port is invalid")
        self.sdk_port = int(sdk_port)
        self.base_url = f"http://{self.host}:{self.sdk_port}"
        self.transport = transport or UrllibJsonTransport(timeout=timeout)

    def info(self):
        return self._get("/info")

    def list_android(self) -> tuple[MytSlot, ...]:
        payload = self._get("/android")
        if isinstance(payload, dict):
            items = payload.get("list")
            if items is None:
                items = payload.get("data")
        else:
            items = payload
        if not isinstance(items, list):
            raise MytSdkError("MYT SDK android payload is invalid")
        if any(not isinstance(item, dict) for item in items):
            raise MytSdkError("MYT SDK android payload contains an invalid slot")
        slots = tuple(
            self._parse_slot(item)
            for item in items
            if str(item.get("status") or "").lower() == "running"
        )
        return slots

    def _get(self, path: str):
        payload = self.transport.request("GET", f"{self.base_url}{path}", None)
        if isinstance(payload, dict) and "code" in payload:
            raw_code = payload.get("code")
            if raw_code not in (0, "0", None):
                code = int(raw_code) if str(raw_code).lstrip("-").isdigit() else None
                message = (
                    f"MYT SDK returned error code {code}"
                    if code is not None
                    else "MYT SDK returned an error"
                )
                raise MytSdkError(message, sdk_code=code)
            return payload.get("data", payload)
        return payload

    def _parse_slot(self, item: dict[str, object]) -> MytSlot:
        slot_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        slot_index = self._integer(item.get("indexNum", item.get("index", 0)))
        if not slot_id or not name or slot_index <= 0:
            raise MytSdkError("MYT SDK running slot identity is invalid")
        adb_port = self._mapped_port(item, "5555")
        if adb_port is None:
            adb_port = self._integer(item.get("adbPort"))
        if not 1 <= adb_port <= 65_535:
            raise MytSdkError("MYT SDK running slot ADB host port is invalid")
        web_port = self._mapped_port(item, "9082")
        if web_port is None:
            web_port = self._integer(item.get("webPort"))
        if not 1 <= web_port <= 65_535:
            raise MytSdkError("MYT SDK running slot web host port is invalid")
        return MytSlot(
            slot_id=slot_id,
            name=name,
            status=str(item.get("status") or "unknown").lower(),
            slot_index=slot_index,
            ip=str(item.get("ip") or ""),
            adb_endpoint=f"{self.host}:{adb_port}",
            web_port=web_port,
            image=str(item.get("image") or ""),
            raw={key: value for key, value in item.items() if key in _HEALTH_FIELDS},
        )

    @classmethod
    def _mapped_port(cls, item: dict[str, object], container_port: str) -> int | None:
        bindings = item.get("portBindings") or {}
        if not isinstance(bindings, dict):
            raise MytSdkError("MYT SDK running slot port bindings are invalid")
        for key, value in bindings.items():
            if str(key).split("/")[0] != container_port:
                continue
            if isinstance(value, list) and value:
                value = value[0]
            if isinstance(value, dict):
                for field in ("HostPort", "hostPort", "host_port", "port"):
                    if value.get(field) not in (None, ""):
                        return cls._integer(value[field])
            return cls._integer(value)
        return None

    @staticmethod
    def _integer(value, default: int = 0) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return default

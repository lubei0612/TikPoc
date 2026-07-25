from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

VMOS_API_BASE = "https://api.vmoscloud.com"


@dataclass(frozen=True, repr=False)
class VmosCredentials:
    access_key: str
    secret_key: str

    @classmethod
    def from_env_file(cls, path: Path) -> VmosCredentials:
        values = _read_env_file(path)
        access_key = values.get("VMOS_ACCESS_KEY", "").strip()
        secret_key = values.get("VMOS_SECRET_KEY", "").strip()
        if not access_key or not secret_key:
            raise ValueError("VMOS_ACCESS_KEY and VMOS_SECRET_KEY are required")
        return cls(access_key, secret_key)


@dataclass(frozen=True)
class VmosSignedRequest:
    path: str
    body: str
    headers: Mapping[str, str]


@dataclass(frozen=True)
class VmosInstance:
    pad_code: str
    online: bool
    model: str


@dataclass(frozen=True, repr=False)
class VmosAdbLease:
    pad_code: str
    command: str
    adb_endpoint: str
    expires_at: str
    connection_key: str


VmosTransport = Callable[[VmosSignedRequest], str]


def sign_vmos_request(
    *,
    access_key: str,
    secret_key: str,
    path: str,
    payload: Mapping[str, object],
    timestamp: str,
) -> VmosSignedRequest:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    signature = hashlib.sha256(
        f"{secret_key}{timestamp}{path}{body}".encode()
    ).hexdigest()
    return VmosSignedRequest(
        path=path,
        body=body,
        headers={
            "X-Access-Key": access_key,
            "X-Timestamp": timestamp,
            "X-Sign": signature,
            "Content-Type": "application/json",
        },
    )


def sign_vmos_v4_request(
    *,
    access_key: str,
    secret_key: str,
    path: str,
    payload: Mapping[str, object],
    x_date: str,
) -> VmosSignedRequest:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    host = "api.vmoscloud.com"
    content_type = "application/json;charset=UTF-8"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    canonical = (
        f"host:{host}\n"
        f"x-date:{x_date}\n"
        f"content-type:{content_type}\n"
        f"signedHeaders:{signed_headers}\n"
        f"x-content-sha256:{body_hash}"
    )
    scope = f"{x_date[:8]}/armcloud-paas/request"
    string_to_sign = (
        f"HMAC-SHA256\n{x_date}\n{scope}\n"
        f"{hashlib.sha256(canonical.encode()).hexdigest()}"
    )
    signing_key = _hmac(secret_key.encode(), x_date[:8])
    signing_key = _hmac(signing_key, "armcloud-paas")
    signing_key = _hmac(signing_key, "request")
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    authorization = (
        f"HMAC-SHA256 Credential={access_key}, SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return VmosSignedRequest(
        path=path,
        body=body,
        headers={
            "content-type": content_type,
            "x-host": host,
            "x-date": x_date,
            "x-content-sha256": body_hash,
            "authorization": authorization,
        },
    )


class VmosCloudClient:
    def __init__(
        self,
        credentials: VmosCredentials,
        *,
        transport: VmosTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._credentials = credentials
        self._transport = transport or _send_request
        self._clock = clock

    def list_instances(self) -> tuple[VmosInstance, ...]:
        data = self._post("/vcpcloud/api/padApi/infos", {"page": 1, "rows": 100})
        if isinstance(data, dict):
            rows = data.get("rows") or data.get("list") or ()
        else:
            rows = data
        if not isinstance(rows, list):
            raise RuntimeError("VMOS instance response has no bounded row list")
        return tuple(_parse_instance(row) for row in rows if isinstance(row, dict))

    def open_adb(self, pad_code: str, *, expire_minutes: int = 1440) -> VmosAdbLease:
        normalized = str(pad_code).strip()
        if not normalized:
            raise ValueError("pad_code is required")
        if not 1440 <= expire_minutes <= 10080:
            raise ValueError("expire_minutes must be between 1440 and 10080")
        data = self._post(
            "/vcpcloud/api/padApi/adb",
            {
                "padCode": normalized,
                "enable": True,
                "expireMinutes": expire_minutes,
            },
        )
        if not isinstance(data, dict):
            raise RuntimeError("VMOS ADB response is incomplete")
        adb_command = str(data.get("adb") or "").strip()
        endpoint = adb_command.removeprefix("adb connect ").strip()
        if not endpoint or not str(data.get("command") or "").strip():
            raise RuntimeError("VMOS ADB response is incomplete")
        return VmosAdbLease(
            pad_code=str(data.get("padCode") or normalized),
            command=str(data["command"]),
            adb_endpoint=endpoint,
            expires_at=str(data.get("expireTime") or ""),
            connection_key=str(data.get("key") or ""),
        )

    def start_app(self, pad_code: str, package: str) -> str:
        data = self._post(
            "/vcpcloud/api/padApi/startApp",
            {"pkgName": package.strip(), "padCodes": [pad_code.strip()]},
        )
        first = data[0] if isinstance(data, list) and data else data
        if not isinstance(first, dict) or not first.get("taskId"):
            raise RuntimeError("VMOS start-app response is incomplete")
        return str(first["taskId"])

    def _post(self, path: str, payload: Mapping[str, object]) -> object:
        x_date = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
        request = sign_vmos_v4_request(
            access_key=self._credentials.access_key,
            secret_key=self._credentials.secret_key,
            path=path,
            payload=payload,
            x_date=x_date,
        )
        try:
            response = json.loads(self._transport(request))
        except (json.JSONDecodeError, OSError) as error:
            raise RuntimeError("VMOS API returned an invalid response") from error
        if not isinstance(response, dict):
            raise RuntimeError("VMOS API returned an invalid response")
        code = int(response.get("code") or 0)
        if code != 200:
            raise RuntimeError(f"VMOS API request failed with code {code}")
        return response.get("data")


def _parse_instance(row: Mapping[str, object]) -> VmosInstance:
    pad_code = str(row.get("padCode") or "").strip()
    if not pad_code:
        raise RuntimeError("VMOS instance row is missing padCode")
    return VmosInstance(
        pad_code=pad_code,
        online=str(row.get("online") or row.get("vmStatus") or "0") == "1",
        model=str(row.get("brandModel") or row.get("model") or ""),
    )


def _hmac(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode(), hashlib.sha256).digest()


def _send_request(request: VmosSignedRequest) -> str:
    http_request = Request(
        VMOS_API_BASE + request.path,
        data=request.body.encode(),
        headers=dict(request.headers),
        method="POST",
    )
    with urlopen(http_request, timeout=30) as response:
        return response.read().decode()


def _read_env_file(path: Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.is_file():
        raise ValueError(f"VMOS env file does not exist: {env_path}")
    mode = env_path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ValueError("VMOS env file must be owner-readable only")
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = os.path.expandvars(value.strip().strip("\"'"))
    return values

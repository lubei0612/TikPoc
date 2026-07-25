import hashlib
import json

import pytest

from tikpoc.vmos_cloud import (
    VmosCloudClient,
    VmosCredentials,
    VmosSignedRequest,
    sign_vmos_request,
    sign_vmos_v4_request,
)


def test_vmos_v2_signer_emits_required_headers_without_secret() -> None:
    request = sign_vmos_request(
        access_key="ACCESS",
        secret_key="SECRET",
        path="/vcpcloud/api/padApi/padInfo",
        payload={"padCode": "ACP250625501MXP"},
        timestamp="1784919000",
    )
    expected = hashlib.sha256(
        ("SECRET1784919000/vcpcloud/api/padApi/padInfo" + request.body).encode()
    ).hexdigest()

    assert request.headers == {
        "X-Access-Key": "ACCESS",
        "X-Timestamp": "1784919000",
        "X-Sign": expected,
        "Content-Type": "application/json",
    }
    assert request.body == '{"padCode":"ACP250625501MXP"}'
    assert "SECRET" not in repr(request)


def test_vmos_v4_signer_supports_existing_account_keys() -> None:
    request = sign_vmos_v4_request(
        access_key="ACCESS",
        secret_key="SECRET",
        path="/vcpcloud/api/padApi/padInfo",
        payload={"padCode": "ACP250625501MXP"},
        x_date="20260725T025000Z",
    )

    assert request.headers["x-host"] == "api.vmoscloud.com"
    assert request.headers["x-date"] == "20260725T025000Z"
    assert (
        request.headers["x-content-sha256"]
        == hashlib.sha256(request.body.encode()).hexdigest()
    )
    assert request.headers["authorization"].startswith(
        "HMAC-SHA256 Credential=ACCESS, "
    )
    assert "SECRET" not in repr(request)


def test_vmos_client_parses_instances_and_adb_lease() -> None:
    requests: list[VmosSignedRequest] = []

    def transport(request: VmosSignedRequest) -> str:
        requests.append(request)
        if request.path.endswith("/infos"):
            data = {
                "pageData": [
                    {
                        "padCode": "ACP250625501MXP",
                        "online": 1,
                        "brandModel": "SM-G996U1(8G)",
                    }
                ]
            }
        else:
            data = {
                "padCode": "ACP250625501MXP",
                "command": "ssh s@example -p 1824 -L 57203:localhost:1 -Nf",
                "expireTime": "2026-07-26 12:00:00",
                "enable": True,
                "key": "PRIVATE-CONNECTION-KEY",
                "adb": "adb connect localhost:57203",
            }
        return json.dumps({"code": 200, "msg": "success", "data": data})

    client = VmosCloudClient(
        VmosCredentials("ACCESS", "SECRET"),
        transport=transport,
        clock=lambda: 1784919000,
    )

    instance = client.list_instances()[0]
    lease = client.open_adb(instance.pad_code, expire_minutes=2880)

    assert instance.pad_code == "ACP250625501MXP"
    assert instance.online is True
    assert instance.model == "SM-G996U1(8G)"
    assert lease.adb_endpoint == "localhost:57203"
    assert lease.command.startswith("ssh ")
    assert "PRIVATE-CONNECTION-KEY" not in repr(lease)
    assert requests[0].headers["X-Timestamp"] == "1784919000"
    assert "X-Sign" in requests[0].headers
    assert "authorization" not in requests[0].headers
    assert requests[1].body == (
        '{"padCode":"ACP250625501MXP","enable":true,"expireMinutes":2880}'
    )


def test_vmos_client_rejects_error_response_without_exposing_credentials() -> None:
    client = VmosCloudClient(
        VmosCredentials("ACCESS", "SECRET"),
        transport=lambda _request: json.dumps(
            {"code": 2019, "msg": "signature failed SECRET"}
        ),
    )

    with pytest.raises(
        RuntimeError, match="VMOS API request failed with code 2019"
    ) as error:
        client.list_instances()

    assert "SECRET" not in str(error.value)


def test_vmos_client_retries_temporary_system_busy_response() -> None:
    responses = iter(
        (
            {"code": 500, "msg": "System is busy"},
            {"code": 500, "msg": "System is busy"},
            {
                "code": 200,
                "msg": "success",
                "data": {
                    "pageData": [
                        {
                            "padCode": "ACP250625501MXP",
                            "online": 1,
                            "brandModel": "SM-G996U1(8G)",
                        }
                    ]
                },
            },
        )
    )
    sleeps: list[float] = []
    client = VmosCloudClient(
        VmosCredentials("ACCESS", "SECRET"),
        transport=lambda _request: json.dumps(next(responses)),
        clock=lambda: 1784919000,
        sleeper=sleeps.append,
    )

    assert client.list_instances()[0].pad_code == "ACP250625501MXP"
    assert sleeps == [0.5, 1.0]


def test_vmos_client_does_not_retry_side_effect_after_system_busy() -> None:
    requests: list[VmosSignedRequest] = []

    def transport(request: VmosSignedRequest) -> str:
        requests.append(request)
        return json.dumps({"code": 500, "msg": "System is busy"})

    client = VmosCloudClient(
        VmosCredentials("ACCESS", "SECRET"),
        transport=transport,
        clock=lambda: 1784919000,
        sleeper=lambda _delay: None,
    )

    with pytest.raises(RuntimeError, match="VMOS API request failed with code 500"):
        client.start_app("ACP250625501MXP", "com.zhiliaoapp.musically")

    assert len(requests) == 1


def test_vmos_client_toggles_online_adb_with_bounded_instance_list() -> None:
    requests: list[VmosSignedRequest] = []

    def transport(request: VmosSignedRequest) -> str:
        requests.append(request)
        return json.dumps(
            {
                "code": 200,
                "data": [
                    {
                        "taskId": 42,
                        "padCode": "ACP250625501MXP",
                        "taskStatus": 1,
                    }
                ],
            }
        )

    client = VmosCloudClient(
        VmosCredentials("ACCESS", "SECRET"),
        transport=transport,
        clock=lambda: 1784919000,
    )

    assert client.set_online_adb("ACP250625501MXP", enabled=True) == "42"
    assert requests[0].path == "/vcpcloud/api/padApi/openOnlineAdb"
    assert requests[0].body == ('{"padCodes":["ACP250625501MXP"],"openStatus":1}')

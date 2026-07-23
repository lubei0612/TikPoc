import pytest
import traceback

from tikpoc import myt as myt_module
from tikpoc.myt import MytClient, MytSdkError, UrllibJsonTransport


class FakeJsonTransport:
    def __init__(self, response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, object]] = []

    def request(self, method: str, url: str, json_body=None):
        self.requests.append((method, url, json_body))
        return self.response


def test_myt_lists_running_slots_with_mapped_adb_ports() -> None:
    transport = FakeJsonTransport(
        {
            "code": 0,
            "data": [
                {
                    "id": "container-1",
                    "name": "T0001",
                    "status": "running",
                    "indexNum": 1,
                    "ip": "172.18.0.2",
                    "image": "android-14",
                    "portBindings": {
                        "5555/tcp": [{"HostPort": "30000"}],
                        "9082/tcp": [{"HostPort": "31000"}],
                    },
                }
            ],
        }
    )
    client = MytClient("192.168.28.114", transport=transport)

    slots = client.list_android()

    assert slots[0].adb_endpoint == "192.168.28.114:30000"
    assert slots[0].web_port == 31000
    assert slots[0].slot_index == 1
    assert slots[0].status == "running"
    assert transport.requests == [("GET", "http://192.168.28.114:8000/android", None)]


def test_myt_unwraps_list_from_nested_sdk_response() -> None:
    transport = FakeJsonTransport(
        {
            "code": "0",
            "data": {
                "list": [
                    {
                        "id": "current",
                        "name": "T0002",
                        "status": "running",
                        "indexNum": 2,
                        "adbPort": 30100,
                        "webPort": 30101,
                    }
                ]
            },
        }
    )

    slot = MytClient("192.168.28.114", transport=transport).list_android()[0]

    assert slot.name == "T0002"
    assert slot.adb_endpoint == "192.168.28.114:30100"


def test_myt_prefers_host_binding_and_excludes_non_running_history() -> None:
    transport = FakeJsonTransport(
        {
            "code": 0,
            "data": [
                {
                    "id": "current",
                    "name": "T0002",
                    "status": "running",
                    "indexNum": 2,
                    "adbPort": 5555,
                    "PINCode": "local-pin",
                    "s5Password": "local-password",
                    "portBindings": {
                        "5555/tcp": [{"HostIp": "", "HostPort": "30100"}],
                        "9082/tcp": [{"HostIp": "", "HostPort": "30101"}],
                    },
                },
                {
                    "id": "old",
                    "name": "T0002-old",
                    "status": "exited",
                    "indexNum": 2,
                    "adbPort": 5555,
                },
            ],
        }
    )

    slots = MytClient("192.168.28.114", transport=transport).list_android()

    assert [slot.slot_id for slot in slots] == ["current"]
    assert slots[0].adb_endpoint == "192.168.28.114:30100"
    assert "PINCode" not in slots[0].raw
    assert "s5Password" not in slots[0].raw


def test_myt_sdk_error_does_not_echo_server_credentials() -> None:
    transport = FakeJsonTransport(
        {"code": 401, "message": "invalid token SECRET_VALUE"}
    )

    with pytest.raises(MytSdkError) as captured:
        MytClient("192.168.28.114", transport=transport).info()

    assert captured.value.sdk_code == 401
    assert "SECRET_VALUE" not in str(captured.value)


def test_myt_info_unwraps_sdk_data() -> None:
    transport = FakeJsonTransport({"code": 0, "data": {"currentVersion": "3.5.2"}})

    assert MytClient("192.168.28.114", transport=transport).info() == {
        "currentVersion": "3.5.2"
    }


def test_myt_http_error_preserves_status_without_response_body(monkeypatch) -> None:
    secret_reason = "".join(("secret", " body"))

    def raise_http_error(request, timeout):
        raise myt_module.HTTPError(request.full_url, 503, secret_reason, {}, None)

    monkeypatch.setattr(myt_module, "urlopen", raise_http_error)

    try:
        UrllibJsonTransport().request("GET", "http://192.168.28.114:8000/info")
    except MytSdkError as error:
        captured = error
        rendered = "".join(traceback.format_exception(error))
    else:
        raise AssertionError("MYT HTTP error was not raised")

    assert captured.status_code == 503
    assert secret_reason not in rendered


def test_myt_rejects_malformed_android_payload() -> None:
    transport = FakeJsonTransport({"code": 0, "data": {"unexpected": []}})

    with pytest.raises(MytSdkError, match="android payload"):
        MytClient("192.168.28.114", transport=transport).list_android()


def test_myt_rejects_running_slot_without_host_ports() -> None:
    transport = FakeJsonTransport(
        {
            "code": 0,
            "data": [
                {
                    "id": "current",
                    "name": "T0001",
                    "status": "running",
                    "indexNum": 1,
                }
            ],
        }
    )

    with pytest.raises(MytSdkError, match="ADB host port"):
        MytClient("192.168.28.114", transport=transport).list_android()


def test_myt_non_numeric_sdk_code_is_redacted() -> None:
    transport = FakeJsonTransport({"code": "TOKEN_SECRET", "message": "TOKEN_SECRET"})

    with pytest.raises(MytSdkError) as captured:
        MytClient("192.168.28.114", transport=transport).info()

    assert "TOKEN_SECRET" not in str(captured.value)

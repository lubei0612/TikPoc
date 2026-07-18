import subprocess

import pytest

from tikpoc.mobile_routes import AdbProfileRouter, AdbRouteError


def test_adb_router_opens_stable_profile_with_warm_activity() -> None:
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = tuple(command)
        captured["kwargs"] = kwargs

    AdbProfileRouter("192.0.2.10:30000", adb_path="/sdk/adb", runner=runner).open(
        "snssdk1233://user/profile/123"
    )

    assert captured["command"] == (
        "/sdk/adb",
        "-s",
        "192.0.2.10:30000",
        "shell",
        "am",
        "start",
        "-W",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "snssdk1233://user/profile/123",
        "com.zhiliaoapp.musically",
    )
    assert captured["kwargs"]["timeout"] == 10


def test_adb_router_redacts_command_failure() -> None:
    def runner(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], stderr="private output")

    with pytest.raises(AdbRouteError, match="ADB route failed") as captured:
        AdbProfileRouter("192.0.2.10:30000", runner=runner).open("TARGET_URI")
    assert "private output" not in str(captured.value)

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceSession:
    device_id: str
    account_id: str
    session_epoch: int
    access_token: str = ""

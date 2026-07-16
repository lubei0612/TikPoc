import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen


DEFAULT_BUSINESS_API_BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"


class BusinessMessagingError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class BusinessToken:
    client_id: str
    client_secret: str
    access_token: str
    refresh_token: str
    access_expires_at: int
    refresh_expires_at: int
    business_id: str

    def access_expired(
        self, *, now: int | None = None, skew_seconds: int = 60
    ) -> bool:
        current = int(time.time()) if now is None else int(now)
        return current + max(0, int(skew_seconds)) >= self.access_expires_at

    def refresh_expired(self, *, now: int | None = None) -> bool:
        current = int(time.time()) if now is None else int(now)
        return current >= self.refresh_expires_at


class JsonTokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> BusinessToken:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("token file must contain an object")
        try:
            return BusinessToken(
                client_id=str(data["client_id"]),
                client_secret=str(data["client_secret"]),
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                access_expires_at=int(data["access_expires_at"]),
                refresh_expires_at=int(data["refresh_expires_at"]),
                business_id=str(data["business_id"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("token file has invalid fields") from error

    def save(self, token: BusinessToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(asdict(token), stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()


class TokenStore(Protocol):
    def load(self) -> BusinessToken: ...

    def save(self, token: BusinessToken) -> None: ...


class BusinessMessagingClient:
    def __init__(
        self,
        token_store: TokenStore,
        *,
        base_url: str = DEFAULT_BUSINESS_API_BASE_URL,
        opener: Callable = urlopen,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.token_store = token_store
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.clock = clock

    def send_text(
        self,
        conversation_id: str,
        text: str,
        *,
        referenced_message_id: str = "",
    ) -> str:
        message = text.strip()
        if not message:
            raise ValueError("message text is empty")
        payload: dict[str, object] = {
            "business_id": self.token_store.load().business_id,
            "recipient_type": "CONVERSATION",
            "recipient": conversation_id,
            "message_type": "TEXT",
            "text": {"body": message},
        }
        if referenced_message_id:
            payload["referenced_message_info"] = {
                "referenced_message_id": referenced_message_id
            }
        response = self._authorized_post("business/message/send/", payload)
        message_id = _message_id(response)
        if not message_id:
            raise BusinessMessagingError(
                "message result is uncertain: missing message_id", uncertain=True
            )
        return message_id

    def send_sender_action(self, conversation_id: str, action: str) -> None:
        normalized_action = action.strip().upper()
        if normalized_action not in {"TYPING", "MARK_READ"}:
            raise ValueError(f"unsupported sender action: {action}")
        token = self.token_store.load()
        self._authorized_post(
            "business/message/send/",
            {
                "business_id": token.business_id,
                "recipient_type": "CONVERSATION",
                "recipient": conversation_id,
                "message_type": "SENDER_ACTION",
                "sender_action": normalized_action,
            },
        )

    def refresh_access_token(self) -> BusinessToken:
        token = self.token_store.load()
        current = int(self.clock())
        if token.refresh_expired(now=current):
            raise BusinessMessagingError("refresh token has expired")
        payload = {
            "client_id": token.client_id,
            "client_secret": token.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
        }
        response = self._post_json("tt_user/oauth2/refresh_token/", payload)
        data = _successful_data(response)
        try:
            refreshed = BusinessToken(
                client_id=token.client_id,
                client_secret=token.client_secret,
                access_token=str(data["access_token"]),
                refresh_token=str(data["refresh_token"]),
                access_expires_at=current + int(data["expires_in"]),
                refresh_expires_at=current + int(data["refresh_token_expires_in"]),
                business_id=str(data.get("open_id") or token.business_id),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BusinessMessagingError("invalid token refresh response") from error
        self.token_store.save(refreshed)
        return refreshed

    def _authorized_post(
        self, path: str, payload: dict[str, object]
    ) -> dict[str, object]:
        token = self.token_store.load()
        if token.access_expired(now=int(self.clock())):
            token = self.refresh_access_token()
        try:
            return self._post_json(path, payload, access_token=token.access_token)
        except HTTPError as error:
            if error.code not in {401, 403}:
                raise BusinessMessagingError(
                    f"TikTok API HTTP error: {error.code}"
                ) from error
        refreshed = self.refresh_access_token()
        try:
            return self._post_json(path, payload, access_token=refreshed.access_token)
        except (HTTPError, OSError) as error:
            raise BusinessMessagingError(
                "message result is uncertain after retry", uncertain=True
            ) from error

    def _post_json(
        self,
        path: str,
        payload: dict[str, object],
        *,
        access_token: str = "",
    ) -> dict[str, object]:
        headers = {"Content-Type": "application/json"}
        if access_token:
            headers["Access-Token"] = access_token
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self.opener(request, timeout=30) as response:
                parsed = json.loads(response.read())
        except HTTPError:
            raise
        except (json.JSONDecodeError, TypeError, OSError) as error:
            raise BusinessMessagingError(
                "message result is uncertain", uncertain=True
            ) from error
        if not isinstance(parsed, dict):
            raise BusinessMessagingError("invalid TikTok API response")
        _successful_data(parsed)
        return parsed


def _successful_data(response: dict[str, object]) -> dict[str, object]:
    try:
        code = int(response.get("code", -1))
    except (TypeError, ValueError) as error:
        raise BusinessMessagingError("invalid TikTok API response code") from error
    if code != 0:
        message = str(response.get("message") or "unknown error")
        raise BusinessMessagingError(f"TikTok API error {code}: {message}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise BusinessMessagingError("invalid TikTok API response data")
    return data


def _message_id(response: dict[str, object]) -> str:
    data = _successful_data(response)
    message = data.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("message_id") or "")

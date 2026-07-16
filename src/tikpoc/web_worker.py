import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .business_messaging import (
    BusinessMessagingClient,
    BusinessMessagingError,
    JsonTokenStore,
)
from .db import Database, WebEvent
from .messaging import AiReplyClient
from .web_accounts import WebAccount, WebAccountRegistry


class TerminalWebEventError(RuntimeError):
    pass


class WebEventWorker:
    def __init__(
        self,
        database: Database,
        registry: WebAccountRegistry,
        reply_client: AiReplyClient,
        *,
        client_factory: Callable[[WebAccount], BusinessMessagingClient] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.database = database
        self.registry = registry
        self.reply_client = reply_client
        self.client_factory = client_factory or (
            lambda account: BusinessMessagingClient(JsonTokenStore(account.token_file))
        )
        self.clock = clock

    def run_one(self) -> bool:
        event = self.database.claim_web_event()
        if event is None:
            return False
        try:
            if event.event_type == "dm_received":
                self._handle_dm(event)
            self.database.finish_web_event(event.id, True)
            self.database.record_runtime_event(
                f"web_event_{event.event_type}_completed", event.account_id
            )
        except TerminalWebEventError as error:
            self.database.finish_web_event(
                event.id,
                False,
                max_attempts=1,
                error_code=type(error).__name__,
            )
            self.database.record_runtime_event(
                f"web_event_{event.event_type}_terminal", event.account_id
            )
        except BusinessMessagingError as error:
            self.database.finish_web_event(
                event.id,
                False,
                max_attempts=1 if error.uncertain else 3,
                error_code=type(error).__name__,
            )
            self.database.record_runtime_event(
                f"web_event_{event.event_type}_api_error", event.account_id
            )
        except (KeyError, TypeError, ValueError, OSError) as error:
            self.database.finish_web_event(
                event.id,
                False,
                error_code=type(error).__name__,
            )
            self.database.record_runtime_event(
                f"web_event_{event.event_type}_invalid", event.account_id
            )
        return True

    def _handle_dm(self, event: WebEvent) -> None:
        account = self.registry.by_account_id(event.account_id)
        if not account.enabled:
            raise TerminalWebEventError("web account is disabled")
        payload = event.payload
        business_id = str(payload.get("business_id") or "")
        conversation_id = str(payload.get("conversation_id") or "")
        inbound_message_id = str(payload.get("message_id") or "")
        if business_id != account.business_id:
            raise TerminalWebEventError("business account does not match event")
        if not conversation_id or not inbound_message_id:
            raise TerminalWebEventError("message identity is incomplete")

        inbound_timestamp_ms = int(payload.get("timestamp_ms") or 0)
        self.database.append_web_message(
            event.account_id,
            conversation_id,
            inbound_message_id,
            direction="inbound",
            message_type=str(payload.get("message_type") or "OTHER"),
            text=str(payload.get("text") or ""),
            timestamp_ms=inbound_timestamp_ms,
            participant_id=str(payload.get("sender_id") or ""),
            participant_username=str(payload.get("sender_username") or ""),
            is_follower=(
                payload.get("is_follower")
                if isinstance(payload.get("is_follower"), bool)
                else None
            ),
        )
        existing_reply = self.database.web_reply_message_id(
            event.account_id, conversation_id, inbound_message_id
        )
        if existing_reply is not None:
            return

        outbound_count = self.database.outbound_web_message_count_since(
            event.account_id,
            conversation_id,
            since_timestamp_ms=inbound_timestamp_ms,
        )
        if outbound_count >= 10:
            raise TerminalWebEventError("TikTok outbound message window limit reached")

        client = self.client_factory(account)
        try:
            client.send_sender_action(conversation_id, "TYPING")
        except BusinessMessagingError:
            pass
        history = self.database.recent_web_messages(
            event.account_id, conversation_id, limit=12
        )
        reply = self.reply_client.reply_conversation(
            history,
            private_channel_hint=account.private_channel_hint,
            max_history_messages=12,
        )
        outbound_message_id = client.send_text(
            conversation_id,
            reply,
            referenced_message_id=inbound_message_id,
        )
        self.database.append_web_message(
            event.account_id,
            conversation_id,
            outbound_message_id,
            direction="outbound",
            message_type="TEXT",
            text=reply,
            timestamp_ms=max(int(self.clock() * 1000), inbound_timestamp_ms),
            in_reply_to_message_id=inbound_message_id,
        )
        try:
            client.send_sender_action(conversation_id, "MARK_READ")
        except BusinessMessagingError:
            pass


def run_web_queue(
    database_path: Path,
    *,
    registry: WebAccountRegistry,
    idle_sleep_seconds: float = 1.0,
    once: bool = False,
    reply_client: AiReplyClient | None = None,
) -> None:
    database = Database(database_path)
    startup_errors = 0
    while True:
        try:
            database.migrate()
            database.recover_stale_web_events()
            break
        except sqlite3.OperationalError as error:
            startup_errors += 1
            _report_database_retry(error, startup_errors)
            time.sleep(_database_retry_delay(startup_errors, idle_sleep_seconds))
    worker = WebEventWorker(
        database,
        registry,
        reply_client or AiReplyClient.from_environment(),
    )
    if once:
        worker.run_one()
        return

    sleep_seconds = max(0.05, float(idle_sleep_seconds))
    database_errors = 0
    while True:
        try:
            processed = worker.run_one()
            database_errors = 0
        except sqlite3.OperationalError as error:
            database_errors += 1
            _report_database_retry(error, database_errors)
            time.sleep(_database_retry_delay(database_errors, sleep_seconds))
            continue
        if not processed:
            time.sleep(sleep_seconds)


def _database_retry_delay(attempts: int, idle_sleep_seconds: float) -> float:
    return min(30.0, max(1.0, float(idle_sleep_seconds) * max(1, attempts)))


def _report_database_retry(error: sqlite3.OperationalError, attempts: int) -> None:
    if attempts == 1 or attempts % 60 == 0:
        print(f"web-worker database unavailable; retrying: {error}", file=sys.stderr)


def start_web_worker_thread(
    database_path: Path,
    *,
    registry: WebAccountRegistry,
    idle_sleep_seconds: float = 1.0,
) -> threading.Thread:
    thread = threading.Thread(
        target=run_web_queue,
        kwargs={
            "database_path": database_path,
            "registry": registry,
            "idle_sleep_seconds": idle_sleep_seconds,
        },
        name="tikpoc-web-worker",
        daemon=True,
    )
    thread.start()
    return thread

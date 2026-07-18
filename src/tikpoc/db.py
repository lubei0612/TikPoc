import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import ProfileMetrics, TaskState


@dataclass(frozen=True)
class Task:
    id: int
    batch_id: str
    target_id: str
    username: str
    state: TaskState
    attempts: int
    checkpoint: str | None
    device_id: str = "default"
    profile_metrics: ProfileMetrics | None = None
    private_account: bool | None = None
    sec_uid: str = ""
    profile_url: str = ""


@dataclass(frozen=True)
class DeviceEvent:
    id: int
    device_id: str
    event_type: str
    dedup_key: str
    payload: dict[str, object]
    attempts: int = 0


@dataclass(frozen=True)
class WebEvent:
    id: int
    account_id: str
    event_type: str
    dedup_key: str
    payload: dict[str, object]
    attempts: int = 0


@dataclass(frozen=True)
class BrowserReplyPlan:
    id: int
    account_id: str
    conversation_id: str
    inbound_fingerprint: str
    participant_username: str
    inbound_text: str
    inbound_timestamp_ms: int
    reply_text: str
    stage: str
    state: str


@dataclass(frozen=True)
class BrowserConversationState:
    account_id: str
    conversation_id: str
    stage: str
    meaningful_turns: int
    auto_reply_count: int
    last_invited_at_ms: int
    contact_captured_at_ms: int
    human_required: bool


def _row_browser_reply_plan(row: sqlite3.Row) -> BrowserReplyPlan:
    return BrowserReplyPlan(
        id=int(row["id"]),
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        inbound_fingerprint=str(row["inbound_fingerprint"]),
        participant_username=str(row["participant_username"]),
        inbound_text=str(row["inbound_text"]),
        inbound_timestamp_ms=int(row["inbound_timestamp_ms"]),
        reply_text=str(row["reply_text"]),
        stage=str(row["stage"]),
        state=str(row["state"]),
    )


def _row_browser_conversation_state(row: sqlite3.Row) -> BrowserConversationState:
    return BrowserConversationState(
        account_id=str(row["account_id"]),
        conversation_id=str(row["conversation_id"]),
        stage=str(row["stage"]),
        meaningful_turns=int(row["meaningful_turns"]),
        auto_reply_count=int(row["auto_reply_count"]),
        last_invited_at_ms=int(row["last_invited_at_ms"]),
        contact_captured_at_ms=int(row["contact_captured_at_ms"]),
        human_required=bool(row["human_required"]),
    )


def _require_identity(*values: str) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("identity values must be nonempty")


_CONVERSATION_STAGE_RANK = {
    "new": 0,
    "engaged": 1,
    "qualified": 2,
    "invited": 3,
    "contact_captured": 4,
    "human_required": 5,
    "closed": 6,
}


def _later_conversation_stage(current: str, proposed: str) -> str:
    if current not in _CONVERSATION_STAGE_RANK:
        raise ValueError(f"invalid stored conversation stage: {current}")
    if proposed not in _CONVERSATION_STAGE_RANK:
        raise ValueError(f"invalid conversation stage: {proposed}")
    if _CONVERSATION_STAGE_RANK[proposed] < _CONVERSATION_STAGE_RANK[current]:
        return current
    return proposed


def _row_profile_metrics(row: sqlite3.Row) -> ProfileMetrics | None:
    values = (row["following_count"], row["followers_count"], row["post_count"])
    if any(value is None for value in values):
        return None
    return ProfileMetrics(
        following=int(values[0]), followers=int(values[1]), posts=int(values[2])
    )


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone()
            if existing is not None:
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(tasks)")
                }
                if "device_id" not in columns:
                    connection.execute("ALTER TABLE tasks RENAME TO tasks_v1")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT 'default',
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    checkpoint TEXT,
                    error_code TEXT,
                    following_count INTEGER,
                    followers_count INTEGER,
                    post_count INTEGER,
                    private_account INTEGER,
                    sec_uid TEXT NOT NULL DEFAULT '',
                    profile_url TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(batch_id, target_id, device_id)
                )
                """
            )
            old = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks_v1'"
            ).fetchone()
            if old is not None:
                connection.execute(
                    """
                    INSERT INTO tasks(
                        id, batch_id, target_id, username, device_id, state, attempts,
                        checkpoint, error_code, created_at, updated_at
                    )
                    SELECT id, batch_id, target_id, username, 'default', state, attempts,
                           checkpoint, error_code, created_at, updated_at
                    FROM tasks_v1
                    """
                )
                connection.execute("DROP TABLE tasks_v1")
            task_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
            }
            optional_task_columns = {
                "following_count": "INTEGER",
                "followers_count": "INTEGER",
                "post_count": "INTEGER",
                "private_account": "INTEGER",
                "sec_uid": "TEXT NOT NULL DEFAULT ''",
                "profile_url": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in optional_task_columns.items():
                if name not in task_columns:
                    connection.execute(
                        f"ALTER TABLE tasks ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_control (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    requested_state TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(device_id, dedup_key)
                )
                """
            )
            device_event_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(device_events)")
            }
            if "attempts" not in device_event_columns:
                connection.execute(
                    "ALTER TABLE device_events ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "next_attempt_at" not in device_event_columns:
                connection.execute(
                    "ALTER TABLE device_events ADD COLUMN next_attempt_at TEXT"
                )
            if "error_code" not in device_event_columns:
                connection.execute(
                    "ALTER TABLE device_events ADD COLUMN error_code TEXT"
                )
            connection.execute(
                """
                UPDATE device_events
                SET next_attempt_at = COALESCE(next_attempt_at, CURRENT_TIMESTAMP)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    dedup_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(account_id, event_type, dedup_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_conversations (
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL DEFAULT '',
                    participant_username TEXT NOT NULL DEFAULT '',
                    is_follower INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(account_id, conversation_id)
                )
                """
            )
            web_conversation_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(web_conversations)")
            }
            conversation_columns = {
                "stage": "TEXT NOT NULL DEFAULT 'new'",
                "meaningful_turns": "INTEGER NOT NULL DEFAULT 0",
                "auto_reply_count": "INTEGER NOT NULL DEFAULT 0",
                "last_invited_at_ms": "INTEGER NOT NULL DEFAULT 0",
                "contact_captured_at_ms": "INTEGER NOT NULL DEFAULT 0",
                "human_required": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, declaration in conversation_columns.items():
                if name not in web_conversation_columns:
                    connection.execute(
                        f"ALTER TABLE web_conversations ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    message_type TEXT NOT NULL,
                    text TEXT NOT NULL DEFAULT '',
                    timestamp_ms INTEGER NOT NULL,
                    in_reply_to_message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(account_id, message_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_reply_plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    inbound_fingerprint TEXT NOT NULL,
                    participant_username TEXT NOT NULL DEFAULT '',
                    inbound_text TEXT NOT NULL DEFAULT '',
                    inbound_timestamp_ms INTEGER NOT NULL,
                    reply_text TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'new',
                    state TEXT NOT NULL DEFAULT 'planning',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(account_id, inbound_fingerprint)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS browser_reply_plans_conversation_idx
                ON browser_reply_plans(account_id, conversation_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_action_leases (
                    account_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    lease_expires_at_ms INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'claimed',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(account_id, action_type, action_key)
                )
                """
            )
            web_message_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(web_messages)")
            }
            if "in_reply_to_message_id" not in web_message_columns:
                connection.execute(
                    """
                    ALTER TABLE web_messages
                    ADD COLUMN in_reply_to_message_id TEXT NOT NULL DEFAULT ''
                    """
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS web_messages_conversation_idx
                ON web_messages(account_id, conversation_id, timestamp_ms, id)
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO worker_control(singleton, requested_state) VALUES (1, 'running')"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    username TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            quota_existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quota_windows'"
            ).fetchone()
            if quota_existing is not None:
                quota_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(quota_windows)")
                }
                if "device_id" not in quota_columns:
                    connection.execute(
                        "ALTER TABLE quota_windows RENAME TO quota_windows_v1"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quota_windows (
                    device_id TEXT NOT NULL DEFAULT 'default',
                    action TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    reserved_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(device_id, action, window_start)
                )
                """
            )
            quota_old = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='quota_windows_v1'"
            ).fetchone()
            if quota_old is not None:
                connection.execute(
                    """
                    INSERT INTO quota_windows(device_id, action, window_start, reserved_count)
                    SELECT 'default', action, window_start, reserved_count FROM quota_windows_v1
                    """
                )
                connection.execute("DROP TABLE quota_windows_v1")

    def insert_task(
        self,
        batch_id: str,
        target_id: str,
        username: str,
        device_id: str = "default",
        *,
        profile_metrics: ProfileMetrics | None = None,
        private_account: bool | None = None,
        sec_uid: str = "",
        profile_url: str = "",
    ) -> int:
        metrics = profile_metrics
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tasks(
                    batch_id, target_id, username, device_id,
                    following_count, followers_count, post_count,
                    private_account, sec_uid, profile_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    target_id,
                    username,
                    device_id,
                    metrics.following if metrics else None,
                    metrics.followers if metrics else None,
                    metrics.posts if metrics else None,
                    None if private_account is None else int(private_account),
                    sec_uid,
                    profile_url,
                ),
            )
            return int(cursor.lastrowid)

    def assign_target_to_devices(
        self,
        batch_id: str,
        target_id: str,
        username: str,
        device_ids: tuple[str, ...],
        *,
        profile_metrics: ProfileMetrics | None = None,
        private_account: bool | None = None,
        sec_uid: str = "",
        profile_url: str = "",
    ) -> tuple[int, ...]:
        return tuple(
            self.insert_task(
                batch_id,
                target_id,
                username,
                device_id,
                profile_metrics=profile_metrics,
                private_account=private_account,
                sec_uid=sec_uid,
                profile_url=profile_url,
            )
            for device_id in device_ids
        )

    def claim_next(self, device_id: str | None = None) -> Task | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            query = """
                SELECT * FROM tasks
                WHERE state IN ('pending', 'retry_wait')
                {device_filter}
                ORDER BY id
                LIMIT 1
                """.format(
                device_filter="AND device_id = ?" if device_id is not None else ""
            )
            row = connection.execute(
                query, (device_id,) if device_id is not None else ()
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE tasks
                SET state = 'running', attempts = attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (row["id"],),
            )
            return Task(
                id=row["id"],
                batch_id=row["batch_id"],
                target_id=row["target_id"],
                username=row["username"],
                state=TaskState.RUNNING,
                attempts=row["attempts"] + 1,
                checkpoint=row["checkpoint"],
                device_id=row["device_id"],
                profile_metrics=_row_profile_metrics(row),
                private_account=(
                    None
                    if row["private_account"] is None
                    else bool(row["private_account"])
                ),
                sec_uid=row["sec_uid"],
                profile_url=row["profile_url"],
            )

    def enqueue_device_event(
        self,
        device_id: str,
        event_type: str,
        dedup_key: str,
        payload: dict[str, object],
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO device_events(
                    device_id, event_type, dedup_key, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    device_id,
                    event_type,
                    dedup_key,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def claim_device_event(self, device_id: str) -> DeviceEvent | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM device_events
                WHERE device_id = ?
                  AND state IN ('pending', 'retry_wait')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                ORDER BY id LIMIT 1
                """,
                (device_id,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE device_events
                SET state='running', attempts=attempts + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (row["id"],),
            )
            return DeviceEvent(
                id=row["id"],
                device_id=row["device_id"],
                event_type=row["event_type"],
                dedup_key=row["dedup_key"],
                payload=json.loads(row["payload_json"]),
                attempts=row["attempts"] + 1,
            )

    def finish_device_event(
        self,
        event_id: int,
        success: bool,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: int = 5,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM device_events WHERE id=? AND state='running'",
                (event_id,),
            ).fetchone()
            if row is None:
                return
            if success:
                state = "completed"
                next_attempt_at = None
            elif row["attempts"] >= max(1, max_attempts):
                state = "failed"
                next_attempt_at = None
            else:
                state = "retry_wait"
                modifier = f"+{max(0, int(retry_delay_seconds))} seconds"
                next_attempt_at = connection.execute(
                    "SELECT datetime('now', ?)", (modifier,)
                ).fetchone()[0]
            connection.execute(
                """
                UPDATE device_events
                SET state=?, next_attempt_at=?, error_code=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state='running'
                """,
                (state, next_attempt_at, error_code, event_id),
            )

    def recover_stale_device_events(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE device_events
                SET state='retry_wait', next_attempt_at=CURRENT_TIMESTAMP,
                    error_code='worker_interrupted', updated_at=CURRENT_TIMESTAMP
                WHERE state='running'
                """
            )
            return cursor.rowcount

    def device_event_state(self, event_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM device_events WHERE id=?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return str(row["state"])

    def enqueue_web_event(
        self,
        account_id: str,
        event_type: str,
        dedup_key: str,
        payload: dict[str, object],
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO web_events(
                    account_id, event_type, dedup_key, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    account_id,
                    event_type,
                    dedup_key,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def claim_web_event(self, account_id: str | None = None) -> WebEvent | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            parameters: tuple[object, ...]
            account_filter = ""
            if account_id is None:
                parameters = ()
            else:
                account_filter = "AND account_id = ?"
                parameters = (account_id,)
            row = connection.execute(
                f"""
                SELECT * FROM web_events
                WHERE state IN ('pending', 'retry_wait')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                  {account_filter}
                ORDER BY id LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE web_events
                SET state='running', attempts=attempts + 1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (row["id"],),
            )
            return WebEvent(
                id=row["id"],
                account_id=row["account_id"],
                event_type=row["event_type"],
                dedup_key=row["dedup_key"],
                payload=json.loads(row["payload_json"]),
                attempts=row["attempts"] + 1,
            )

    def finish_web_event(
        self,
        event_id: int,
        success: bool,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: int = 5,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM web_events WHERE id=? AND state='running'",
                (event_id,),
            ).fetchone()
            if row is None:
                return
            if success:
                state = "completed"
                next_attempt_at = None
            elif row["attempts"] >= max(1, max_attempts):
                state = "failed"
                next_attempt_at = None
            else:
                state = "retry_wait"
                modifier = f"+{max(0, int(retry_delay_seconds))} seconds"
                next_attempt_at = connection.execute(
                    "SELECT datetime('now', ?)", (modifier,)
                ).fetchone()[0]
            connection.execute(
                """
                UPDATE web_events
                SET state=?, next_attempt_at=?, error_code=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state='running'
                """,
                (state, next_attempt_at, error_code, event_id),
            )

    def recover_stale_web_events(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE web_events
                SET state='retry_wait', next_attempt_at=CURRENT_TIMESTAMP,
                    error_code='worker_interrupted', updated_at=CURRENT_TIMESTAMP
                WHERE state='running'
                """
            )
            return cursor.rowcount

    def web_event_state(self, event_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM web_events WHERE id=?", (event_id,)
            ).fetchone()
        if row is None:
            raise KeyError(event_id)
        return str(row["state"])

    def append_web_message(
        self,
        account_id: str,
        conversation_id: str,
        message_id: str,
        *,
        direction: str,
        message_type: str,
        text: str,
        timestamp_ms: int,
        participant_id: str = "",
        participant_username: str = "",
        is_follower: bool | None = None,
        in_reply_to_message_id: str = "",
    ) -> bool:
        follower_value = None if is_follower is None else int(is_follower)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO web_conversations(
                    account_id, conversation_id, participant_id,
                    participant_username, is_follower
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, conversation_id) DO UPDATE SET
                    participant_id=CASE
                        WHEN excluded.participant_id != '' THEN excluded.participant_id
                        ELSE web_conversations.participant_id
                    END,
                    participant_username=CASE
                        WHEN excluded.participant_username != ''
                        THEN excluded.participant_username
                        ELSE web_conversations.participant_username
                    END,
                    is_follower=COALESCE(
                        excluded.is_follower, web_conversations.is_follower
                    ),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    account_id,
                    conversation_id,
                    participant_id,
                    participant_username,
                    follower_value,
                ),
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO web_messages(
                    account_id, conversation_id, message_id, direction,
                    message_type, text, timestamp_ms, in_reply_to_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    conversation_id,
                    message_id,
                    direction,
                    message_type,
                    text,
                    int(timestamp_ms),
                    in_reply_to_message_id,
                ),
            )
            return cursor.rowcount == 1

    def reserve_browser_reply_plan(
        self,
        account_id: str,
        conversation_id: str,
        inbound_fingerprint: str,
        participant_username: str,
        inbound_text: str,
        inbound_timestamp_ms: int,
    ) -> tuple[BrowserReplyPlan, bool]:
        _require_identity(account_id, conversation_id, inbound_fingerprint)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO browser_reply_plans(
                    account_id, conversation_id, inbound_fingerprint,
                    participant_username, inbound_text, inbound_timestamp_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    conversation_id,
                    inbound_fingerprint,
                    participant_username,
                    inbound_text,
                    int(inbound_timestamp_ms),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM browser_reply_plans
                WHERE account_id=? AND inbound_fingerprint=?
                """,
                (account_id, inbound_fingerprint),
            ).fetchone()
            assert row is not None
            return _row_browser_reply_plan(row), cursor.rowcount == 1

    def reserve_browser_inbound_plan(
        self,
        account_id: str,
        conversation_id: str,
        inbound_fingerprint: str,
        participant_username: str,
        inbound_text: str,
        inbound_timestamp_ms: int,
    ) -> tuple[BrowserReplyPlan, bool]:
        _require_identity(
            account_id,
            conversation_id,
            inbound_fingerprint,
            participant_username,
            inbound_text,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO browser_reply_plans(
                    account_id, conversation_id, inbound_fingerprint,
                    participant_username, inbound_text, inbound_timestamp_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    conversation_id,
                    inbound_fingerprint,
                    participant_username,
                    inbound_text,
                    int(inbound_timestamp_ms),
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT * FROM browser_reply_plans
                WHERE account_id=? AND inbound_fingerprint=?
                """,
                (account_id, inbound_fingerprint),
            ).fetchone()
            assert row is not None
            plan = _row_browser_reply_plan(row)
            if not created and plan.state != "planning":
                return plan, False

            connection.execute(
                """
                INSERT INTO web_conversations(
                    account_id, conversation_id, participant_username
                ) VALUES (?, ?, ?)
                ON CONFLICT(account_id, conversation_id) DO UPDATE SET
                    participant_username=CASE
                        WHEN excluded.participant_username != ''
                        THEN excluded.participant_username
                        ELSE web_conversations.participant_username
                    END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (plan.account_id, plan.conversation_id, plan.participant_username),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO web_messages(
                    account_id, conversation_id, message_id, direction,
                    message_type, text, timestamp_ms
                ) VALUES (?, ?, ?, 'inbound', 'TEXT', ?, ?)
                """,
                (
                    plan.account_id,
                    plan.conversation_id,
                    plan.inbound_fingerprint,
                    plan.inbound_text,
                    plan.inbound_timestamp_ms,
                ),
            )
            return plan, created

    def browser_conversation_state(
        self, account_id: str, conversation_id: str
    ) -> BrowserConversationState:
        _require_identity(account_id, conversation_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT account_id, conversation_id, stage, meaningful_turns,
                       auto_reply_count, last_invited_at_ms,
                       contact_captured_at_ms, human_required
                FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, conversation_id),
            ).fetchone()
        if row is None:
            raise KeyError((account_id, conversation_id))
        return _row_browser_conversation_state(row)

    def get_browser_reply_plan(
        self, account_id: str, inbound_fingerprint: str
    ) -> BrowserReplyPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM browser_reply_plans
                WHERE account_id=? AND inbound_fingerprint=?
                """,
                (account_id, inbound_fingerprint),
            ).fetchone()
        return None if row is None else _row_browser_reply_plan(row)

    def browser_reply_plan_by_id(self, plan_id: int) -> BrowserReplyPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?", (plan_id,)
            ).fetchone()
        return None if row is None else _row_browser_reply_plan(row)

    def complete_browser_reply_plan(
        self, plan_id: int, *, reply_text: str, stage: str
    ) -> BrowserReplyPlan:
        if not reply_text.strip() or not stage.strip():
            raise ValueError("reply text and stage must be nonempty")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if row["state"] == "planning":
                connection.execute(
                    """
                    UPDATE browser_reply_plans
                    SET reply_text=?, stage=?, state='planned',
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND state='planning'
                    """,
                    (reply_text, stage, plan_id),
                )
                row = connection.execute(
                    "SELECT * FROM browser_reply_plans WHERE id=?", (plan_id,)
                ).fetchone()
                assert row is not None
            return _row_browser_reply_plan(row)

    def finalize_browser_reply_plan(
        self,
        plan_id: int,
        *,
        reply_text: str,
        plan_stage: str,
        conversation_stage: str,
        meaningful: bool,
        now_ms: int,
    ) -> BrowserReplyPlan:
        if plan_stage not in _CONVERSATION_STAGE_RANK:
            raise ValueError(f"invalid browser reply plan stage: {plan_stage}")
        if not reply_text.strip() and plan_stage not in {"closed", "human_required"}:
            raise ValueError("reply text must be nonempty for an actionable plan")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if row["state"] != "planning":
                return _row_browser_reply_plan(row)
            conversation = connection.execute(
                """
                SELECT stage FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (row["account_id"], row["conversation_id"]),
            ).fetchone()
            if conversation is None:
                raise KeyError((row["account_id"], row["conversation_id"]))
            next_stage = _later_conversation_stage(
                str(conversation["stage"]), conversation_stage
            )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET reply_text=?, stage=?, state='planned',
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state='planning'
                """,
                (reply_text, plan_stage, plan_id),
            )
            connection.execute(
                """
                UPDATE web_conversations
                SET stage=?,
                    meaningful_turns=meaningful_turns + ?,
                    contact_captured_at_ms=CASE
                        WHEN ?='contact_captured' AND contact_captured_at_ms=0
                        THEN ? ELSE contact_captured_at_ms
                    END,
                    human_required=CASE
                        WHEN ?='human_required' THEN 1 ELSE human_required
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND conversation_id=?
                """,
                (
                    next_stage,
                    int(bool(meaningful)),
                    next_stage,
                    int(now_ms),
                    next_stage,
                    row["account_id"],
                    row["conversation_id"],
                ),
            )
            completed = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?", (plan_id,)
            ).fetchone()
            assert completed is not None
            return _row_browser_reply_plan(completed)

    def record_browser_diagnostic_event(
        self,
        account_id: str,
        event_type: str,
        dedup_key: str,
        payload: dict[str, object],
    ) -> bool:
        _require_identity(account_id, event_type, dedup_key)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO web_events(
                    account_id, event_type, dedup_key, payload_json, state
                ) VALUES (?, ?, ?, ?, 'completed')
                """,
                (
                    account_id,
                    event_type,
                    dedup_key,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return cursor.rowcount == 1

    def set_browser_reply_plan_state(self, plan_id: int, state: str) -> None:
        if state not in {"sent", "uncertain", "superseded"}:
            raise ValueError(f"invalid browser reply plan state: {state}")
        allowed_sources = {
            "sent": ("planned", "uncertain", "sent"),
            "uncertain": ("planned", "uncertain"),
            "superseded": ("planned", "uncertain", "superseded"),
        }
        sources = allowed_sources[state]
        placeholders = ", ".join("?" for _ in sources)
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE browser_reply_plans
                SET state=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state IN ({placeholders})
                """,
                (state, plan_id, *sources),
            )

    def record_browser_reply_result(
        self,
        account_id: str,
        plan_id: int,
        state: str,
        *,
        now_ms: int,
        invitation_sent: bool = False,
    ) -> bool:
        _require_identity(account_id)
        allowed_sources = {
            "sent": {"planned", "uncertain", "sent"},
            "uncertain": {"planned", "uncertain"},
            "superseded": {"planned", "uncertain", "superseded"},
        }
        if state not in allowed_sources:
            raise ValueError(f"invalid browser reply result state: {state}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?", (int(plan_id),)
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if row["account_id"] != account_id:
                raise ValueError("browser reply plan belongs to a different account")
            current_state = str(row["state"])
            if current_state not in allowed_sources[state]:
                return False
            if current_state == state:
                return True
            if state != "sent":
                cursor = connection.execute(
                    """
                    UPDATE browser_reply_plans
                    SET state=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND state=?
                    """,
                    (state, int(plan_id), current_state),
                )
                return cursor.rowcount == 1

            reply_text = str(row["reply_text"])
            if not reply_text.strip():
                return False
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET state='sent', updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state=?
                """,
                (int(plan_id), current_state),
            )
            message_timestamp_ms = max(int(now_ms), int(row["inbound_timestamp_ms"]))
            connection.execute(
                """
                INSERT INTO web_messages(
                    account_id, conversation_id, message_id, direction,
                    message_type, text, timestamp_ms, in_reply_to_message_id
                ) VALUES (?, ?, ?, 'outbound', 'TEXT', ?, ?, ?)
                """,
                (
                    account_id,
                    row["conversation_id"],
                    f"browser-reply-plan:{int(plan_id)}",
                    reply_text,
                    message_timestamp_ms,
                    row["inbound_fingerprint"],
                ),
            )
            conversation = connection.execute(
                """
                SELECT stage FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, row["conversation_id"]),
            ).fetchone()
            if conversation is None:
                raise KeyError((account_id, row["conversation_id"]))
            next_stage = _later_conversation_stage(
                str(conversation["stage"]), str(row["stage"])
            )
            connection.execute(
                """
                UPDATE web_conversations
                SET stage=?, auto_reply_count=auto_reply_count + 1,
                    last_invited_at_ms=CASE
                        WHEN ? THEN ? ELSE last_invited_at_ms
                    END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND conversation_id=?
                """,
                (
                    next_stage,
                    int(bool(invitation_sent)),
                    int(now_ms),
                    account_id,
                    row["conversation_id"],
                ),
            )
            return True

    def claim_browser_action(
        self,
        account_id: str,
        action_type: str,
        action_key: str,
        owner_id: str,
        now_ms: int,
        lease_seconds: int = 30,
    ) -> bool:
        _require_identity(account_id, action_type, action_key, owner_id)
        expires_at_ms = int(now_ms) + max(1, int(lease_seconds)) * 1_000
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO browser_action_leases(
                    account_id, action_type, action_key, owner_id,
                    lease_expires_at_ms, state
                ) VALUES (?, ?, ?, ?, ?, 'claimed')
                ON CONFLICT(account_id, action_type, action_key) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    lease_expires_at_ms=excluded.lease_expires_at_ms,
                    state='claimed',
                    updated_at=CURRENT_TIMESTAMP
                WHERE browser_action_leases.state != 'completed'
                  AND browser_action_leases.lease_expires_at_ms <= ?
                """,
                (
                    account_id,
                    action_type,
                    action_key,
                    owner_id,
                    expires_at_ms,
                    int(now_ms),
                ),
            )
            return cursor.rowcount == 1

    def finish_browser_action(
        self,
        account_id: str,
        action_type: str,
        action_key: str,
        owner_id: str,
        state: str,
    ) -> bool:
        _require_identity(account_id, action_type, action_key, owner_id)
        if state not in {"completed", "uncertain", "superseded"}:
            raise ValueError(f"invalid browser action state: {state}")
        completed_guard = "" if state == "completed" else "AND state != 'completed'"
        with self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE browser_action_leases
                SET state=?, updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND action_type=? AND action_key=?
                  AND owner_id=? {completed_guard}
                """,
                (state, account_id, action_type, action_key, owner_id),
            )
            return cursor.rowcount == 1

    def recent_web_messages(
        self, account_id: str, conversation_id: str, *, limit: int = 20
    ) -> list[dict[str, object]]:
        bounded_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, direction, message_type, text, timestamp_ms
                FROM (
                    SELECT id, message_id, direction, message_type, text, timestamp_ms
                    FROM web_messages
                    WHERE account_id=? AND conversation_id=?
                    ORDER BY timestamp_ms DESC, id DESC
                    LIMIT ?
                )
                ORDER BY timestamp_ms, id
                """,
                (account_id, conversation_id, bounded_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def outbound_web_message_count_since(
        self,
        account_id: str,
        conversation_id: str,
        *,
        since_timestamp_ms: int,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM web_messages
                WHERE account_id=? AND conversation_id=?
                  AND direction='outbound' AND timestamp_ms>=?
                """,
                (account_id, conversation_id, int(since_timestamp_ms)),
            ).fetchone()
        return int(row[0])

    def web_reply_message_id(
        self, account_id: str, conversation_id: str, inbound_message_id: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT message_id
                FROM web_messages
                WHERE account_id=? AND conversation_id=?
                  AND direction='outbound' AND in_reply_to_message_id=?
                ORDER BY id LIMIT 1
                """,
                (account_id, conversation_id, inbound_message_id),
            ).fetchone()
        return None if row is None else str(row["message_id"])

    def checkpoint(self, task_id: int, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET checkpoint = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (value, task_id),
            )

    def finish(
        self,
        task_id: int,
        state: TaskState,
        error_code: str | None = None,
    ) -> None:
        if state not in {
            TaskState.COMPLETED,
            TaskState.SKIPPED,
            TaskState.RETRY_WAIT,
            TaskState.FAILED,
        }:
            raise ValueError(f"invalid terminal task state: {state}")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE tasks
                SET state = ?, error_code = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND state = 'running'
                """,
                (state.value, error_code, task_id),
            )

    def recover_stale_tasks(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET state = 'retry_wait', error_code = 'worker_interrupted',
                    updated_at = CURRENT_TIMESTAMP
                WHERE state = 'running'
                """
            )
            return cursor.rowcount

    def task_state(self, task_id: int) -> TaskState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(task_id)
        return TaskState(row["state"])

    def count_by_state(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM tasks GROUP BY state ORDER BY state"
            ).fetchall()
        return {row["state"]: row["count"] for row in rows}

    def dashboard_snapshot(self) -> dict[str, object]:
        counts = self.count_by_state()
        total = sum(counts.values())
        processed = sum(
            counts.get(state, 0) for state in ("completed", "skipped", "failed")
        )
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT username, checkpoint, attempts, updated_at
                FROM tasks WHERE state = 'running' ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
        return {
            "total": total,
            "processed": processed,
            "counts": counts,
            "current": dict(current) if current else None,
            "control": self.worker_control(),
        }

    def recent_tasks(self, limit: int = 10) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT username, state, attempts, checkpoint, error_code, updated_at
                FROM tasks
                WHERE state != 'pending'
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def worker_control(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT requested_state FROM worker_control WHERE singleton = 1"
            ).fetchone()
        return row["requested_state"] if row else "running"

    def set_worker_control(self, state: str) -> None:
        if state not in {"running", "paused", "stopped"}:
            raise ValueError(f"invalid worker control state: {state}")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE worker_control
                SET requested_state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE singleton = 1
                """,
                (state,),
            )

    def record_runtime_event(
        self, event_type: str, username: str | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_events(event_type, username) VALUES (?, ?)",
                (event_type, username),
            )

    def reserve_action(
        self, action: str, window_start: str, limit: int, device_id: str = "default"
    ) -> bool:
        if limit <= 0:
            return False
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO quota_windows(
                    device_id, action, window_start, reserved_count
                ) VALUES (?, ?, ?, 0)
                """,
                (device_id, action, window_start),
            )
            cursor = connection.execute(
                """
                UPDATE quota_windows
                SET reserved_count = reserved_count + 1
                WHERE device_id = ? AND action = ? AND window_start = ?
                  AND reserved_count < ?
                """,
                (device_id, action, window_start, limit),
            )
            return cursor.rowcount == 1

    def latest_runtime_event(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_type, username, created_at
                FROM runtime_events ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

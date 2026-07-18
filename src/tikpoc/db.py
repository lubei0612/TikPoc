import hashlib
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
    plan_origin: str = "ai"
    source_inbound_fingerprint: str = ""
    invitation_included: bool = False
    invitation_evidence_known: bool = False


class BrowserConversationBusy(RuntimeError):
    def __init__(self, plan: BrowserReplyPlan) -> None:
        self.plan = plan
        super().__init__(
            f"conversation has an unresolved uncertain browser reply plan: {plan.id}"
        )


class OperatorCommandConflict(ValueError):
    pass


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
        plan_origin=str(row["plan_origin"]),
        source_inbound_fingerprint=str(row["source_inbound_fingerprint"]),
        invitation_included=bool(row["invitation_included"]),
        invitation_evidence_known=bool(row["invitation_evidence_known"]),
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
_CONVERSATION_STAGE_ALIASES = {"qualifying": "qualified"}
_FUNNEL_STAGES = (
    "dm_inbound",
    "engaged",
    "qualified",
    "invited",
    "contact_captured",
    "human_required",
)
_SALE_STATUSES = {"pending", "confirmed", "refunded", "cancelled"}


def _canonical_request_json(request: dict[str, object]) -> str:
    return json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _legacy_operator_request(
    command_type: str, result: dict[str, object]
) -> dict[str, object] | None:
    if command_type == "return_to_ai":
        return {}
    field_sets = {
        "takeover": ("reason",),
        "manual_reply": ("inbound_fingerprint", "reply_text"),
        "sale": ("amount_minor", "currency", "status", "occurred_at_ms"),
        "ai_enable": ("ai_enabled",),
        "followback_enable": ("followback_enabled",),
    }
    fields = field_sets.get(command_type)
    if fields is None or any(field not in result for field in fields):
        return None
    if command_type == "takeover" and not isinstance(result["reason"], str):
        return None
    if command_type == "manual_reply" and not all(
        isinstance(result[field], str)
        for field in ("inbound_fingerprint", "reply_text")
    ):
        return None
    if command_type == "sale" and (
        not isinstance(result["amount_minor"], int)
        or isinstance(result["amount_minor"], bool)
        or not isinstance(result["currency"], str)
        or not isinstance(result["status"], str)
        or not isinstance(result["occurred_at_ms"], int)
        or isinstance(result["occurred_at_ms"], bool)
    ):
        return None
    if command_type == "ai_enable":
        return (
            {"enabled": result["ai_enabled"]}
            if isinstance(result["ai_enabled"], bool)
            else None
        )
    if command_type == "followback_enable":
        return (
            {"enabled": result["followback_enabled"]}
            if isinstance(result["followback_enabled"], bool)
            else None
        )
    return {field: result[field] for field in fields}


def _canonical_conversation_stage(stage: str) -> str:
    value = str(stage).strip()
    canonical = _CONVERSATION_STAGE_ALIASES.get(value, value)
    if canonical not in _CONVERSATION_STAGE_RANK:
        raise ValueError(f"invalid conversation stage: {stage}")
    return canonical


def _later_conversation_stage(current: str, proposed: str) -> str:
    current = _canonical_conversation_stage(current)
    proposed = _canonical_conversation_stage(proposed)
    if _CONVERSATION_STAGE_RANK[proposed] < _CONVERSATION_STAGE_RANK[current]:
        return current
    return proposed


def _browser_reply_budget_counts(
    connection: sqlite3.Connection,
    account_id: str,
    conversation_id: str,
    *,
    excluding_plan_id: int,
) -> tuple[int, int]:
    conversation = connection.execute(
        """
        SELECT auto_reply_count FROM web_conversations
        WHERE account_id=? AND conversation_id=?
        """,
        (account_id, conversation_id),
    ).fetchone()
    if conversation is None:
        raise KeyError((account_id, conversation_id))
    outbound_count = connection.execute(
        """
        SELECT COUNT(*) FROM web_messages
        WHERE account_id=? AND conversation_id=? AND direction='outbound'
        """,
        (account_id, conversation_id),
    ).fetchone()[0]
    reserved_count = connection.execute(
        """
        SELECT COUNT(*) FROM browser_reply_plans
        WHERE account_id=? AND conversation_id=? AND id != ?
          AND state IN ('planned', 'uncertain') AND TRIM(reply_text) != ''
        """,
        (account_id, conversation_id, int(excluding_plan_id)),
    ).fetchone()[0]
    confirmed_count = max(int(conversation["auto_reply_count"]), int(outbound_count))
    return confirmed_count, int(reserved_count)


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
                UPDATE web_conversations SET stage='qualified'
                WHERE stage='qualifying'
                """
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
                    plan_origin TEXT NOT NULL DEFAULT 'ai',
                    source_inbound_fingerprint TEXT NOT NULL DEFAULT '',
                    invitation_included INTEGER NOT NULL DEFAULT 0,
                    invitation_evidence_known INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(account_id, inbound_fingerprint)
                )
                """
            )
            browser_reply_plan_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(browser_reply_plans)")
            }
            if "invitation_included" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN invitation_included INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "invitation_evidence_known" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN invitation_evidence_known INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "created_at_ms" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN created_at_ms INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "sent_at_ms" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN sent_at_ms INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "plan_origin" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN plan_origin TEXT NOT NULL DEFAULT 'ai'
                    """
                )
            if "source_inbound_fingerprint" not in browser_reply_plan_columns:
                connection.execute(
                    """
                    ALTER TABLE browser_reply_plans
                    ADD COLUMN source_inbound_fingerprint TEXT NOT NULL DEFAULT ''
                    """
                )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET created_at_ms=CAST(strftime('%s', created_at) AS INTEGER) * 1000
                WHERE created_at_ms=0
                """
            )
            connection.execute(
                """
                UPDATE browser_reply_plans SET stage='qualified'
                WHERE stage='qualifying'
                """
            )
            connection.execute(
                """
                UPDATE browser_reply_plans SET invitation_evidence_known=1
                WHERE invitation_included=1
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_lead_commands (
                    command_type TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    command_id TEXT NOT NULL,
                    request_json TEXT,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(command_type, account_id, conversation_id, command_id)
                )
                """
            )
            operator_command_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(operator_lead_commands)"
                )
            }
            if "request_json" not in operator_command_columns:
                connection.execute(
                    "ALTER TABLE operator_lead_commands ADD COLUMN request_json TEXT"
                )
            legacy_commands = connection.execute(
                """
                SELECT command_type, account_id, conversation_id, command_id,
                       result_json
                FROM operator_lead_commands WHERE request_json IS NULL
                """
            ).fetchall()
            for command in legacy_commands:
                try:
                    result = json.loads(str(command["result_json"]))
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(result, dict):
                    continue
                request = _legacy_operator_request(str(command["command_type"]), result)
                if request is None:
                    continue
                connection.execute(
                    """
                    UPDATE operator_lead_commands SET request_json=?
                    WHERE command_type=? AND account_id=? AND conversation_id=?
                      AND command_id=? AND request_json IS NULL
                    """,
                    (
                        _canonical_request_json(request),
                        command["command_type"],
                        command["account_id"],
                        command["conversation_id"],
                        command["command_id"],
                    ),
                )

            connection.execute(
                "DROP INDEX IF EXISTS browser_reply_plans_manual_source_uq"
            )
            manual_sources: dict[int, set[tuple[str, str, str]]] = {}
            for command in connection.execute(
                """
                SELECT account_id, conversation_id, result_json
                FROM operator_lead_commands WHERE command_type='manual_reply'
                """
            ):
                try:
                    result = json.loads(str(command["result_json"]))
                    plan_id = int(result["plan_id"])
                    source = str(result["inbound_fingerprint"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if plan_id <= 0 or not source.strip():
                    continue
                manual_sources.setdefault(plan_id, set()).add(
                    (
                        str(command["account_id"]),
                        str(command["conversation_id"]),
                        source,
                    )
                )
            for plan_id, identities in manual_sources.items():
                if len(identities) != 1:
                    continue
                account_id, conversation_id, source = next(iter(identities))
                connection.execute(
                    """
                    UPDATE browser_reply_plans
                    SET plan_origin='manual', source_inbound_fingerprint=?
                    WHERE id=? AND account_id=? AND conversation_id=?
                      AND inbound_fingerprint LIKE 'operator-manual:%'
                    """,
                    (source, plan_id, account_id, conversation_id),
                )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET source_inbound_fingerprint=inbound_fingerprint
                WHERE plan_origin='ai' AND source_inbound_fingerprint=''
                  AND inbound_fingerprint NOT LIKE 'operator-manual:%'
                """
            )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET state='superseded', updated_at=CURRENT_TIMESTAMP
                WHERE plan_origin='ai' AND inbound_fingerprint LIKE 'operator-manual:%'
                  AND state IN ('planning', 'planned')
                """
            )
            structured_manual_plans = connection.execute(
                """
                SELECT id, account_id, conversation_id,
                       source_inbound_fingerprint, state
                FROM browser_reply_plans
                WHERE plan_origin='manual' AND source_inbound_fingerprint != ''
                  AND state IN ('planning', 'planned', 'uncertain', 'sent')
                ORDER BY account_id, conversation_id, source_inbound_fingerprint, id
                """
            ).fetchall()
            manual_plan_groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
            for plan in structured_manual_plans:
                identity = (
                    str(plan["account_id"]),
                    str(plan["conversation_id"]),
                    str(plan["source_inbound_fingerprint"]),
                )
                manual_plan_groups.setdefault(identity, []).append(plan)
            for plans in manual_plan_groups.values():
                sent_exists = any(str(plan["state"]) == "sent" for plan in plans)
                unresolved = [
                    plan
                    for plan in plans
                    if str(plan["state"]) in {"planning", "planned", "uncertain"}
                ]
                duplicates = unresolved if sent_exists else unresolved[1:]
                for plan in duplicates:
                    connection.execute(
                        """
                        UPDATE browser_reply_plans
                        SET state='superseded', updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (int(plan["id"]),),
                    )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS browser_reply_plans_manual_source_uq
                ON browser_reply_plans(
                    account_id, conversation_id, plan_origin,
                    source_inbound_fingerprint
                )
                WHERE plan_origin='manual' AND source_inbound_fingerprint != ''
                  AND state IN ('planning', 'planned', 'uncertain')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS web_account_settings (
                    account_id TEXT PRIMARY KEY,
                    ai_enabled INTEGER NOT NULL,
                    followback_enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_funnel_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    participant_username TEXT NOT NULL,
                    conversation_id TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL,
                    UNIQUE(account_id, participant_username, stage, source_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS lead_funnel_recent_idx
                ON lead_funnel_events(occurred_at_ms DESC, event_id DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS lead_sales (
                    sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    participant_username TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS browser_account_health (
                    account_id TEXT NOT NULL,
                    page_role TEXT NOT NULL,
                    device_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(account_id, page_role)
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
                    participant_username, inbound_text, inbound_timestamp_ms,
                    plan_origin, source_inbound_fingerprint, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, 'ai', ?,
                          CAST(strftime('%s', 'now') AS INTEGER) * 1000)
                """,
                (
                    account_id,
                    conversation_id,
                    inbound_fingerprint,
                    participant_username,
                    inbound_text,
                    int(inbound_timestamp_ms),
                    inbound_fingerprint,
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
            row = connection.execute(
                """
                SELECT * FROM browser_reply_plans
                WHERE account_id=? AND inbound_fingerprint=?
                """,
                (account_id, inbound_fingerprint),
            ).fetchone()
            if row is None:
                unresolved = connection.execute(
                    """
                    SELECT * FROM browser_reply_plans
                    WHERE account_id=? AND conversation_id=?
                      AND state='uncertain' AND TRIM(reply_text) != ''
                    ORDER BY id LIMIT 1
                    """,
                    (account_id, conversation_id),
                ).fetchone()
                if unresolved is not None:
                    raise BrowserConversationBusy(_row_browser_reply_plan(unresolved))
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO browser_reply_plans(
                    account_id, conversation_id, inbound_fingerprint,
                    participant_username, inbound_text, inbound_timestamp_ms,
                    plan_origin, source_inbound_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, 'ai', ?)
                """,
                (
                    account_id,
                    conversation_id,
                    inbound_fingerprint,
                    participant_username,
                    inbound_text,
                    int(inbound_timestamp_ms),
                    inbound_fingerprint,
                ),
            )
            created = cursor.rowcount == 1
            if row is None:
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

    def browser_reply_budget_counts(
        self,
        account_id: str,
        conversation_id: str,
        *,
        excluding_plan_id: int,
    ) -> tuple[int, int]:
        _require_identity(account_id, conversation_id)
        with self._connect() as connection:
            return _browser_reply_budget_counts(
                connection,
                account_id,
                conversation_id,
                excluding_plan_id=int(excluding_plan_id),
            )

    def browser_reply_budget_usage(
        self,
        account_id: str,
        conversation_id: str,
        *,
        excluding_plan_id: int,
    ) -> int:
        confirmed, reserved = self.browser_reply_budget_counts(
            account_id,
            conversation_id,
            excluding_plan_id=excluding_plan_id,
        )
        return confirmed + reserved

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
        if not reply_text.strip():
            raise ValueError("reply text must be nonempty")
        stage = _canonical_conversation_stage(stage)
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
        max_auto_replies: int,
        contact_captured: bool = False,
        invitation_included: bool = False,
    ) -> BrowserReplyPlan:
        plan_stage = _canonical_conversation_stage(plan_stage)
        conversation_stage = _canonical_conversation_stage(conversation_stage)
        if not reply_text.strip() and plan_stage not in {"closed", "human_required"}:
            raise ValueError("reply text must be nonempty for an actionable plan")
        if int(max_auto_replies) < 1:
            raise ValueError("max auto replies must be positive")
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
            confirmed_replies, reserved_replies = _browser_reply_budget_counts(
                connection,
                str(row["account_id"]),
                str(row["conversation_id"]),
                excluding_plan_id=int(plan_id),
            )
            if reply_text.strip() and confirmed_replies + reserved_replies >= int(
                max_auto_replies
            ):
                reply_text = ""
                plan_stage = "closed"
                if confirmed_replies >= int(max_auto_replies):
                    conversation_stage = "closed"
                invitation_included = False
            next_stage = _later_conversation_stage(
                str(conversation["stage"]), conversation_stage
            )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET reply_text=?, stage=?, state='planned', invitation_included=?,
                    invitation_evidence_known=1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state='planning'
                """,
                (reply_text, plan_stage, int(bool(invitation_included)), plan_id),
            )
            connection.execute(
                """
                UPDATE web_conversations
                SET stage=?,
                    meaningful_turns=meaningful_turns + ?,
                    contact_captured_at_ms=CASE
                        WHEN ? AND contact_captured_at_ms=0
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
                    int(bool(contact_captured)),
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

    def reconcile_browser_reply_invitation_evidence(
        self,
        account_id: str,
        plan_id: int,
        *,
        private_channel_hint: str,
    ) -> BrowserReplyPlan:
        _require_identity(account_id)
        normalized_hint = " ".join(str(private_channel_hint).split())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_reply_plans WHERE id=?", (int(plan_id),)
            ).fetchone()
            if row is None:
                raise KeyError(plan_id)
            if row["account_id"] != account_id:
                raise ValueError("browser reply plan belongs to a different account")
            if not bool(row["invitation_evidence_known"]):
                normalized_reply = " ".join(str(row["reply_text"]).split())
                invitation_included = bool(
                    normalized_hint and normalized_hint in normalized_reply
                )
                connection.execute(
                    """
                    UPDATE browser_reply_plans
                    SET invitation_included=?, invitation_evidence_known=1,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND invitation_evidence_known=0
                    """,
                    (int(invitation_included), int(plan_id)),
                )
                row = connection.execute(
                    "SELECT * FROM browser_reply_plans WHERE id=?", (int(plan_id),)
                ).fetchone()
                assert row is not None
            return _row_browser_reply_plan(row)

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
                SET state='sent', sent_at_ms=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND state=?
                """,
                (int(now_ms), int(plan_id), current_state),
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
                    int(
                        bool(row["invitation_evidence_known"])
                        and bool(row["invitation_included"])
                    ),
                    int(now_ms),
                    account_id,
                    row["conversation_id"],
                ),
            )
            return True

    def record_lead_funnel_event(
        self,
        account_id: str,
        participant_username: str,
        stage: str,
        source_key: str,
        *,
        conversation_id: str = "",
        occurred_at_ms: int,
    ) -> bool:
        _require_identity(account_id, participant_username, source_key)
        normalized_stage = str(stage).strip()
        if normalized_stage not in _FUNNEL_STAGES:
            raise ValueError(f"invalid lead funnel stage: {stage}")
        if int(occurred_at_ms) < 0:
            raise ValueError("lead funnel timestamp must be nonnegative")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO lead_funnel_events(
                    account_id, participant_username, conversation_id,
                    stage, source_key, occurred_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id.strip(),
                    participant_username.strip(),
                    conversation_id.strip(),
                    normalized_stage,
                    source_key.strip(),
                    int(occurred_at_ms),
                ),
            )
            return cursor.rowcount == 1

    def lead_funnel_snapshot(
        self, *, account_ids: tuple[str, ...] | None = None
    ) -> dict[str, int]:
        if account_ids == ():
            return {stage: 0 for stage in _FUNNEL_STAGES}
        with self._connect() as connection:
            if account_ids is None:
                rows = connection.execute(
                    """
                    SELECT stage, COUNT(*) AS count
                    FROM lead_funnel_events GROUP BY stage
                    """
                ).fetchall()
            else:
                bounded_ids = tuple(dict.fromkeys(account_ids))[:100]
                placeholders = ", ".join("?" for _ in bounded_ids)
                rows = connection.execute(
                    f"""
                    SELECT stage, COUNT(*) AS count
                    FROM lead_funnel_events
                    WHERE account_id IN ({placeholders})
                    GROUP BY stage
                    """,
                    bounded_ids,
                ).fetchall()
        counts = {str(row["stage"]): int(row["count"]) for row in rows}
        return {stage: counts.get(stage, 0) for stage in _FUNNEL_STAGES}

    def recent_leads(self, *, limit: int = 20) -> list[dict[str, object]]:
        bounded_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT account_id, participant_username, conversation_id,
                           stage, occurred_at_ms,
                           MAX(occurred_at_ms) OVER (
                               PARTITION BY account_id, participant_username
                           ) AS last_activity_at_ms,
                           ROW_NUMBER() OVER (
                               PARTITION BY account_id, participant_username
                               ORDER BY CASE stage
                                   WHEN 'human_required' THEN 5
                                   WHEN 'contact_captured' THEN 4
                                   WHEN 'invited' THEN 3
                                   WHEN 'qualified' THEN 2
                                   WHEN 'engaged' THEN 1
                                   ELSE 0
                               END DESC, occurred_at_ms DESC, event_id DESC
                           ) AS rank
                    FROM lead_funnel_events
                )
                SELECT account_id, participant_username, conversation_id,
                       stage, last_activity_at_ms AS occurred_at_ms
                FROM ranked WHERE rank=1
                ORDER BY occurred_at_ms DESC, account_id, participant_username
                LIMIT ?
                """,
                (bounded_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_lead_sale(
        self,
        account_id: str,
        participant_username: str,
        *,
        amount_minor: int,
        currency: str,
        status: str,
        occurred_at_ms: int,
    ) -> int:
        _require_identity(account_id, participant_username)
        if int(amount_minor) <= 0:
            raise ValueError("sale amount must be a positive minor-unit integer")
        if len(currency) != 3 or not currency.isascii() or not currency.isupper():
            raise ValueError("sale currency must be three uppercase ASCII letters")
        if status not in _SALE_STATUSES:
            raise ValueError(f"invalid sale status: {status}")
        if int(occurred_at_ms) < 0:
            raise ValueError("sale timestamp must be nonnegative")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO lead_sales(
                    account_id, participant_username, amount_minor,
                    currency, status, occurred_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id.strip(),
                    participant_username.strip(),
                    int(amount_minor),
                    currency,
                    status,
                    int(occurred_at_ms),
                ),
            )
            return int(cursor.lastrowid)

    def lead_sales_snapshot(
        self, *, account_ids: tuple[str, ...] | None = None
    ) -> dict[str, object]:
        if account_ids == ():
            return {"by_status": {}, "confirmed_revenue_minor": {}, "sales": 0}
        with self._connect() as connection:
            if account_ids is None:
                rows = connection.execute(
                    """
                    SELECT status, currency, COUNT(*) AS count,
                           SUM(amount_minor) AS amount_minor
                    FROM lead_sales GROUP BY status, currency
                    """
                ).fetchall()
            else:
                bounded_ids = tuple(dict.fromkeys(account_ids))[:100]
                placeholders = ", ".join("?" for _ in bounded_ids)
                rows = connection.execute(
                    f"""
                    SELECT status, currency, COUNT(*) AS count,
                           SUM(amount_minor) AS amount_minor
                    FROM lead_sales
                    WHERE account_id IN ({placeholders})
                    GROUP BY status, currency
                    """,
                    bounded_ids,
                ).fetchall()
        by_status: dict[str, int] = {}
        revenue: dict[str, int] = {}
        sales = 0
        for row in rows:
            count = int(row["count"])
            status = str(row["status"])
            sales += count
            by_status[status] = by_status.get(status, 0) + count
            if status == "confirmed":
                currency = str(row["currency"])
                revenue[currency] = revenue.get(currency, 0) + int(row["amount_minor"])
        return {
            "by_status": by_status,
            "confirmed_revenue_minor": revenue,
            "sales": sales,
        }

    @staticmethod
    def _stored_operator_result(
        connection: sqlite3.Connection,
        command_type: str,
        account_id: str,
        conversation_id: str,
        command_id: str,
        request: dict[str, object],
    ) -> dict[str, object] | None:
        row = connection.execute(
            """
            SELECT request_json, result_json FROM operator_lead_commands
            WHERE command_type=? AND account_id=? AND conversation_id=?
              AND command_id=?
            """,
            (command_type, account_id, conversation_id, command_id),
        ).fetchone()
        if row is None:
            return None
        if row["request_json"] is None or str(
            row["request_json"]
        ) != _canonical_request_json(request):
            raise OperatorCommandConflict(
                "command_id was already used with different request content"
            )
        return json.loads(str(row["result_json"]))

    @staticmethod
    def _store_operator_result(
        connection: sqlite3.Connection,
        command_type: str,
        account_id: str,
        conversation_id: str,
        command_id: str,
        request: dict[str, object],
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO operator_lead_commands(
                command_type, account_id, conversation_id, command_id,
                request_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                command_type,
                account_id,
                conversation_id,
                command_id,
                _canonical_request_json(request),
                json.dumps(result, ensure_ascii=False, sort_keys=True),
            ),
        )

    def account_operator_settings(
        self,
        account_id: str,
        *,
        default_ai_enabled: bool,
        default_followback_enabled: bool,
    ) -> dict[str, bool]:
        _require_identity(account_id)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO web_account_settings(
                    account_id, ai_enabled, followback_enabled
                ) VALUES (?, ?, ?)
                """,
                (
                    account_id,
                    int(default_ai_enabled),
                    int(default_followback_enabled),
                ),
            )
            row = connection.execute(
                """
                SELECT ai_enabled, followback_enabled FROM web_account_settings
                WHERE account_id=?
                """,
                (account_id,),
            ).fetchone()
        assert row is not None
        return {
            "ai_enabled": bool(row["ai_enabled"]),
            "followback_enabled": bool(row["followback_enabled"]),
        }

    def set_account_operator_setting(
        self,
        account_id: str,
        command_id: str,
        *,
        setting: str,
        enabled: bool,
        default_ai_enabled: bool,
        default_followback_enabled: bool,
    ) -> dict[str, object]:
        _require_identity(account_id, command_id)
        fields = {"ai": "ai_enabled", "followback": "followback_enabled"}
        if setting not in fields:
            raise ValueError("invalid account setting")
        command_type = f"{setting}_enable"
        request: dict[str, object] = {"enabled": bool(enabled)}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = self._stored_operator_result(
                connection, command_type, account_id, "", command_id, request
            )
            if stored is not None:
                return stored
            connection.execute(
                """
                INSERT OR IGNORE INTO web_account_settings(
                    account_id, ai_enabled, followback_enabled
                ) VALUES (?, ?, ?)
                """,
                (
                    account_id,
                    int(default_ai_enabled),
                    int(default_followback_enabled),
                ),
            )
            field = fields[setting]
            connection.execute(
                f"""
                UPDATE web_account_settings SET {field}=?, updated_at=CURRENT_TIMESTAMP
                WHERE account_id=?
                """,
                (int(enabled), account_id),
            )
            result: dict[str, object] = {
                "account_id": account_id,
                field: bool(enabled),
            }
            self._store_operator_result(
                connection,
                command_type,
                account_id,
                "",
                command_id,
                request,
                result,
            )
            return result

    def lead_conversations(
        self, *, account_ids: tuple[str, ...], limit: int = 20
    ) -> list[dict[str, object]]:
        if not account_ids:
            return []
        bounded_limit = max(1, min(int(limit), 100))
        placeholders = ", ".join("?" for _ in account_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.account_id, c.conversation_id, c.participant_username,
                       c.stage, c.human_required,
                       COALESCE((
                           SELECT SUBSTR(m.text, 1, 160) FROM web_messages m
                           WHERE m.account_id=c.account_id
                             AND m.conversation_id=c.conversation_id
                           ORDER BY m.timestamp_ms DESC, m.id DESC LIMIT 1
                       ), '') AS last_message_preview,
                       COALESCE((
                           SELECT m.timestamp_ms FROM web_messages m
                           WHERE m.account_id=c.account_id
                             AND m.conversation_id=c.conversation_id
                           ORDER BY m.timestamp_ms DESC, m.id DESC LIMIT 1
                ), 0) AS last_message_at_ms
                FROM web_conversations c
                WHERE c.account_id IN ({placeholders})
                ORDER BY last_message_at_ms DESC, c.account_id, c.conversation_id
                LIMIT ?
                """,
                (*account_ids, bounded_limit),
            ).fetchall()
        return [
            {
                **dict(row),
                "human_required": bool(row["human_required"]),
            }
            for row in rows
        ]

    def selected_lead(
        self,
        account_id: str,
        conversation_id: str,
        *,
        history_limit: int,
        inbound_fingerprint: str = "",
    ) -> dict[str, object]:
        state = self.browser_conversation_state(account_id, conversation_id)
        messages = self.recent_web_messages(
            account_id, conversation_id, limit=history_limit
        )
        draft: dict[str, object] | None = None
        if inbound_fingerprint:
            with self._connect() as connection:
                manual_row = connection.execute(
                    """
                    SELECT * FROM browser_reply_plans
                    WHERE account_id=? AND conversation_id=?
                      AND plan_origin='manual' AND source_inbound_fingerprint=?
                    ORDER BY id LIMIT 1
                    """,
                    (account_id, conversation_id, inbound_fingerprint),
                ).fetchone()
            plan = (
                _row_browser_reply_plan(manual_row)
                if manual_row is not None
                else self.get_browser_reply_plan(account_id, inbound_fingerprint)
            )
            if plan is not None and plan.conversation_id == conversation_id:
                draft = {
                    "plan_id": plan.id,
                    "inbound_fingerprint": plan.source_inbound_fingerprint,
                    "reply_text": plan.reply_text,
                    "state": plan.state,
                }
        return {
            "account_id": account_id,
            "conversation_id": conversation_id,
            "stage": state.stage,
            "human_required": state.human_required,
            "messages": messages,
            "draft": draft,
        }

    def takeover_lead(
        self,
        account_id: str,
        conversation_id: str,
        command_id: str,
        *,
        reason: str,
        occurred_at_ms: int,
    ) -> dict[str, object]:
        _require_identity(account_id, conversation_id, command_id, reason)
        request: dict[str, object] = {"reason": reason}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = self._stored_operator_result(
                connection,
                "takeover",
                account_id,
                conversation_id,
                command_id,
                request,
            )
            if stored is not None:
                return stored
            row = connection.execute(
                """
                SELECT participant_username, stage, human_required
                FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError((account_id, conversation_id))
            current_stage = _canonical_conversation_stage(str(row["stage"]))
            if current_stage == "closed":
                result: dict[str, object] = {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "stage": current_stage,
                    "human_required": bool(row["human_required"]),
                    "reason": reason,
                }
                self._store_operator_result(
                    connection,
                    "takeover",
                    account_id,
                    conversation_id,
                    command_id,
                    request,
                    result,
                )
                return result
            next_stage = _later_conversation_stage(current_stage, "human_required")
            connection.execute(
                """
                UPDATE web_conversations
                SET stage=?, human_required=1,
                    updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND conversation_id=?
                """,
                (next_stage, account_id, conversation_id),
            )
            connection.execute(
                """
                UPDATE browser_reply_plans
                SET state='superseded', updated_at=CURRENT_TIMESTAMP
                WHERE account_id=? AND conversation_id=?
                  AND state IN ('planning', 'planned')
                """,
                (account_id, conversation_id),
            )
            participant = str(row["participant_username"])
            if participant:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO lead_funnel_events(
                        account_id, participant_username, conversation_id,
                        stage, source_key, occurred_at_ms
                    ) VALUES (?, ?, ?, 'human_required', ?, ?)
                    """,
                    (
                        account_id,
                        participant,
                        conversation_id,
                        f"operator:{command_id}",
                        int(occurred_at_ms),
                    ),
                )
            result: dict[str, object] = {
                "account_id": account_id,
                "conversation_id": conversation_id,
                "stage": next_stage,
                "human_required": True,
                "reason": reason,
            }
            self._store_operator_result(
                connection,
                "takeover",
                account_id,
                conversation_id,
                command_id,
                request,
                result,
            )
            return result

    def return_lead_to_ai(
        self,
        account_id: str,
        conversation_id: str,
        command_id: str,
        *,
        account_ai_enabled: bool,
    ) -> dict[str, object]:
        _require_identity(account_id, conversation_id, command_id)
        request: dict[str, object] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = self._stored_operator_result(
                connection,
                "return_to_ai",
                account_id,
                conversation_id,
                command_id,
                request,
            )
            if stored is not None:
                return stored
            if not account_ai_enabled:
                raise ValueError("account AI replies are disabled")
            row = connection.execute(
                """
                SELECT stage, human_required FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, conversation_id),
            ).fetchone()
            if row is None:
                raise KeyError((account_id, conversation_id))
            if str(row["stage"]) in {
                "contact_captured",
                "human_required",
                "closed",
            } or bool(row["human_required"]):
                raise ValueError("conversation is in a terminal policy state")
            uncertain = connection.execute(
                """
                SELECT 1 FROM browser_reply_plans
                WHERE account_id=? AND conversation_id=? AND state='uncertain'
                LIMIT 1
                """,
                (account_id, conversation_id),
            ).fetchone()
            if uncertain is not None:
                raise ValueError("conversation has an uncertain browser send")
            result: dict[str, object] = {
                "account_id": account_id,
                "conversation_id": conversation_id,
                "stage": str(row["stage"]),
                "ai_enabled": True,
            }
            self._store_operator_result(
                connection,
                "return_to_ai",
                account_id,
                conversation_id,
                command_id,
                request,
                result,
            )
            return result

    def create_manual_reply_plan(
        self,
        account_id: str,
        conversation_id: str,
        command_id: str,
        *,
        inbound_fingerprint: str,
        reply_text: str,
        now_ms: int,
    ) -> dict[str, object]:
        _require_identity(
            account_id,
            conversation_id,
            command_id,
            inbound_fingerprint,
        )
        request: dict[str, object] = {
            "inbound_fingerprint": inbound_fingerprint,
            "reply_text": reply_text,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = self._stored_operator_result(
                connection,
                "manual_reply",
                account_id,
                conversation_id,
                command_id,
                request,
            )
            if stored is not None:
                return stored
            plan_identity = _canonical_request_json(
                {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "source_inbound_fingerprint": inbound_fingerprint,
                }
            )
            plan_fingerprint = (
                "manual:" + hashlib.sha256(plan_identity.encode("utf-8")).hexdigest()
            )
            existing = connection.execute(
                """
                SELECT * FROM browser_reply_plans
                WHERE account_id=? AND conversation_id=? AND plan_origin='manual'
                  AND source_inbound_fingerprint=?
                ORDER BY id LIMIT 1
                """,
                (account_id, conversation_id, inbound_fingerprint),
            ).fetchone()
            if existing is not None:
                result: dict[str, object] = {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "plan_id": int(existing["id"]),
                    "inbound_fingerprint": inbound_fingerprint,
                    "reply_text": str(existing["reply_text"]),
                    "state": str(existing["state"]),
                }
                self._store_operator_result(
                    connection,
                    "manual_reply",
                    account_id,
                    conversation_id,
                    command_id,
                    request,
                    result,
                )
                return result
            if not str(reply_text).strip():
                raise ValueError("manual reply text must be nonempty")
            uncertain = connection.execute(
                """
                SELECT 1 FROM browser_reply_plans
                WHERE account_id=? AND conversation_id=? AND state='uncertain'
                  AND TRIM(reply_text) != ''
                LIMIT 1
                """,
                (account_id, conversation_id),
            ).fetchone()
            if uncertain is not None:
                raise ValueError("conversation has an uncertain browser send")
            inbound = connection.execute(
                """
                SELECT text, timestamp_ms FROM web_messages
                WHERE account_id=? AND conversation_id=? AND message_id=?
                  AND direction='inbound'
                """,
                (account_id, conversation_id, inbound_fingerprint),
            ).fetchone()
            conversation = connection.execute(
                """
                SELECT participant_username, stage FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, conversation_id),
            ).fetchone()
            if inbound is None or conversation is None:
                raise KeyError((account_id, conversation_id, inbound_fingerprint))
            if str(conversation["stage"]) != "human_required":
                raise ValueError("manual reply requires human takeover")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO browser_reply_plans(
                    account_id, conversation_id, inbound_fingerprint,
                    participant_username, inbound_text, inbound_timestamp_ms,
                    reply_text, stage, state, invitation_evidence_known,
                    created_at_ms, plan_origin, source_inbound_fingerprint
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', 1, ?, 'manual', ?)
                """,
                (
                    account_id,
                    conversation_id,
                    plan_fingerprint,
                    str(conversation["participant_username"]),
                    str(inbound["text"]),
                    int(inbound["timestamp_ms"]),
                    reply_text,
                    str(conversation["stage"]),
                    int(now_ms),
                    inbound_fingerprint,
                ),
            )
            if cursor.rowcount != 1:
                existing = connection.execute(
                    """
                    SELECT * FROM browser_reply_plans
                    WHERE account_id=? AND conversation_id=? AND plan_origin='manual'
                      AND source_inbound_fingerprint=?
                    ORDER BY id LIMIT 1
                    """,
                    (account_id, conversation_id, inbound_fingerprint),
                ).fetchone()
                assert existing is not None
                result = {
                    "account_id": account_id,
                    "conversation_id": conversation_id,
                    "plan_id": int(existing["id"]),
                    "inbound_fingerprint": inbound_fingerprint,
                    "reply_text": str(existing["reply_text"]),
                    "state": str(existing["state"]),
                }
                self._store_operator_result(
                    connection,
                    "manual_reply",
                    account_id,
                    conversation_id,
                    command_id,
                    request,
                    result,
                )
                return result
            result: dict[str, object] = {
                "account_id": account_id,
                "conversation_id": conversation_id,
                "plan_id": int(cursor.lastrowid),
                "inbound_fingerprint": inbound_fingerprint,
                "reply_text": reply_text,
                "state": "planned",
            }
            self._store_operator_result(
                connection,
                "manual_reply",
                account_id,
                conversation_id,
                command_id,
                request,
                result,
            )
            return result

    def claim_browser_dm_action(
        self,
        account_id: str,
        action_key: str,
        owner_id: str,
        now_ms: int,
        lease_seconds: int = 30,
        *,
        default_ai_enabled: bool,
        account_ai_allowed: bool = True,
    ) -> bool:
        _require_identity(account_id, action_key, owner_id)
        prefix = "dm_send:"
        raw_plan_id = action_key[len(prefix) :] if action_key.startswith(prefix) else ""
        if (
            not raw_plan_id
            or raw_plan_id[0] == "0"
            or any(character < "0" or character > "9" for character in raw_plan_id)
        ):
            return False
        plan_id = int(raw_plan_id)
        if plan_id <= 0:
            return False
        expires_at_ms = int(now_ms) + max(1, int(lease_seconds)) * 1_000
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT plan.plan_origin, plan.state,
                       conversation.stage, conversation.human_required,
                       COALESCE(settings.ai_enabled, ?) AS ai_enabled
                FROM browser_reply_plans AS plan
                JOIN web_conversations AS conversation
                  ON conversation.account_id=plan.account_id
                 AND conversation.conversation_id=plan.conversation_id
                LEFT JOIN web_account_settings AS settings
                  ON settings.account_id=plan.account_id
                WHERE plan.id=? AND plan.account_id=?
                """,
                (int(default_ai_enabled), plan_id, account_id),
            ).fetchone()
            if row is None or str(row["state"]) != "planned":
                return False
            origin = str(row["plan_origin"])
            stage = _canonical_conversation_stage(str(row["stage"]))
            if origin == "manual":
                permitted = stage == "human_required" and bool(row["human_required"])
            elif origin == "ai":
                permitted = (
                    account_ai_allowed
                    and bool(row["ai_enabled"])
                    and not bool(row["human_required"])
                    and stage not in {"contact_captured", "human_required", "closed"}
                )
            else:
                permitted = False
            if not permitted:
                return False
            cursor = connection.execute(
                """
                INSERT INTO browser_action_leases(
                    account_id, action_type, action_key, owner_id,
                    lease_expires_at_ms, state
                ) VALUES (?, 'dm_send', ?, ?, ?, 'claimed')
                ON CONFLICT(account_id, action_type, action_key) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    lease_expires_at_ms=excluded.lease_expires_at_ms,
                    state='claimed',
                    updated_at=CURRENT_TIMESTAMP
                WHERE browser_action_leases.state != 'completed'
                  AND browser_action_leases.lease_expires_at_ms <= ?
                """,
                (account_id, action_key, owner_id, expires_at_ms, int(now_ms)),
            )
            return cursor.rowcount == 1

    def record_lead_sale_command(
        self,
        account_id: str,
        conversation_id: str,
        command_id: str,
        *,
        amount_minor: int,
        currency: str,
        status: str,
        occurred_at_ms: int,
    ) -> dict[str, object]:
        _require_identity(account_id, conversation_id, command_id)
        if int(amount_minor) <= 0:
            raise ValueError("sale amount must be a positive minor-unit integer")
        if len(currency) != 3 or not currency.isascii() or not currency.isupper():
            raise ValueError("sale currency must be three uppercase ASCII letters")
        if status not in _SALE_STATUSES:
            raise ValueError(f"invalid sale status: {status}")
        request: dict[str, object] = {
            "amount_minor": int(amount_minor),
            "currency": currency,
            "status": status,
            "occurred_at_ms": int(occurred_at_ms),
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = self._stored_operator_result(
                connection,
                "sale",
                account_id,
                conversation_id,
                command_id,
                request,
            )
            if stored is not None:
                return stored
            row = connection.execute(
                """
                SELECT participant_username FROM web_conversations
                WHERE account_id=? AND conversation_id=?
                """,
                (account_id, conversation_id),
            ).fetchone()
            if row is None or not str(row["participant_username"]).strip():
                raise KeyError((account_id, conversation_id))
            cursor = connection.execute(
                """
                INSERT INTO lead_sales(
                    account_id, participant_username, amount_minor,
                    currency, status, occurred_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    str(row["participant_username"]),
                    int(amount_minor),
                    currency,
                    status,
                    int(occurred_at_ms),
                ),
            )
            result: dict[str, object] = {
                "sale_id": int(cursor.lastrowid),
                "account_id": account_id,
                "conversation_id": conversation_id,
                "amount_minor": int(amount_minor),
                "currency": currency,
                "status": status,
                "occurred_at_ms": int(occurred_at_ms),
            }
            self._store_operator_result(
                connection,
                "sale",
                account_id,
                conversation_id,
                command_id,
                request,
                result,
            )
            return result

    def conversation_ai_available(self, account_id: str, conversation_id: str) -> bool:
        try:
            state = self.browser_conversation_state(account_id, conversation_id)
        except KeyError:
            return True
        return not state.human_required and state.stage not in {
            "contact_captured",
            "human_required",
            "closed",
        }

    def upsert_browser_health(
        self,
        account_id: str,
        page_role: str,
        *,
        device_id: str,
        status: str,
        observed_at_ms: int,
        detail: str = "",
    ) -> None:
        _require_identity(account_id, page_role, status)
        if int(observed_at_ms) < 0:
            raise ValueError("browser health timestamp must be nonnegative")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO browser_account_health(
                    account_id, page_role, device_id, status,
                    observed_at_ms, detail
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, page_role) DO UPDATE SET
                    device_id=excluded.device_id,
                    status=excluded.status,
                    observed_at_ms=excluded.observed_at_ms,
                    detail=excluded.detail
                WHERE excluded.observed_at_ms >= browser_account_health.observed_at_ms
                """,
                (
                    account_id.strip(),
                    page_role.strip(),
                    device_id.strip(),
                    status.strip(),
                    int(observed_at_ms),
                    detail.strip(),
                ),
            )

    def browser_health_snapshot(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT account_id, page_role, device_id, status,
                       observed_at_ms, detail
                FROM browser_account_health
                ORDER BY account_id, page_role
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def reply_latency_snapshot(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sent_at_ms - created_at_ms AS latency_ms
                FROM browser_reply_plans
                WHERE state='sent' AND sent_at_ms >= created_at_ms
                  AND created_at_ms > 0
                ORDER BY latency_ms
                """
            ).fetchall()
        latencies = [int(row["latency_ms"]) for row in rows]
        if not latencies:
            return {"confirmed_replies": 0, "median_ms": 0, "p90_ms": 0}
        middle = len(latencies) // 2
        median_ms = (
            latencies[middle]
            if len(latencies) % 2
            else (latencies[middle - 1] + latencies[middle]) // 2
        )
        p90_index = max(0, (9 * len(latencies) + 9) // 10 - 1)
        return {
            "confirmed_replies": len(latencies),
            "median_ms": median_ms,
            "p90_ms": latencies[p90_index],
        }

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

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from .acquisition_models import (
    ActionPlan,
    ActionPacingState,
    ActionPlanState,
    ActionResult,
    AssignmentPhase,
    AssignmentTransition,
    AssignmentStage,
    AssignmentStageTiming,
    DeviceDiagnostics,
    OutcomeKind,
    PoolImport,
    PoolTarget,
    ProfileAccessState,
    ProfileSnapshot,
    QuotaWindow,
    RoundAssignment,
    RoundCompletion,
)
from .capacity import AssignmentTiming, RoundCapacityAudit
from .importer import Target, target_identity_key
from .models import ProfileMetrics
from .rules import evaluate_profile


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_ALLOWED_PHASE_TRANSITIONS = {
    AssignmentPhase.PROFILE_OPENING: {
        AssignmentPhase.IDENTITY_CONFIRMED,
        AssignmentPhase.DEFERRED,
        AssignmentPhase.SKIPPED,
    },
    AssignmentPhase.IDENTITY_CONFIRMED: {
        AssignmentPhase.WAITING_SNAPSHOT,
        AssignmentPhase.VIDEO_OPENING,
        AssignmentPhase.COMPLETED,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.WAITING_SNAPSHOT: {
        AssignmentPhase.VIDEO_OPENING,
        AssignmentPhase.COMPLETED,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.VIDEO_OPENING: {
        AssignmentPhase.VIDEO_CONFIRMED,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.VIDEO_CONFIRMED: {
        AssignmentPhase.QUOTA_RESERVED,
        AssignmentPhase.ACTION_RECONCILING,
        AssignmentPhase.COMPLETED,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.QUOTA_RESERVED: {
        AssignmentPhase.ACTION_EXECUTING,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.ACTION_EXECUTING: {
        AssignmentPhase.ACTION_RECONCILING,
        AssignmentPhase.COMPLETED,
        AssignmentPhase.DEFERRED,
    },
    AssignmentPhase.ACTION_RECONCILING: {
        AssignmentPhase.ACTION_EXECUTING,
        AssignmentPhase.COMPLETED,
        AssignmentPhase.DEFERRED,
    },
}
_CAPACITY_QUOTA_LIMITS = {
    OutcomeKind.LIKE: 100,
    OutcomeKind.FAVORITE: 14,
    OutcomeKind.REPOST: 25,
}


class DeviceWorkerLeaseLost(RuntimeError):
    pass


def _clock_ms() -> int:
    return int(time.time() * 1000)


class AcquisitionRepository:
    def __init__(
        self,
        path: Path,
        *,
        clock_ms: Callable[[], int] = _clock_ms,
    ) -> None:
        self.path = path
        self.clock_ms = clock_ms

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _connect_read_only(self) -> Iterator[sqlite3.Connection]:
        database_uri = f"{self.path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(database_uri, timeout=30, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM sqlite_schema
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()
            is not None
        )

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS target_pools (
                    pool_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    source_checksum TEXT NOT NULL UNIQUE,
                    unique_targets INTEGER NOT NULL,
                    source_rows INTEGER NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pool_targets (
                    pool_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    sec_uid TEXT NOT NULL,
                    username TEXT NOT NULL,
                    profile_url TEXT NOT NULL,
                    source_video_id TEXT NOT NULL,
                    source_line_numbers_json TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(pool_id, identity_key),
                    FOREIGN KEY(pool_id) REFERENCES target_pools(pool_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS exposure_rounds (
                    round_id TEXT PRIMARY KEY,
                    pool_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    starts_at_ms INTEGER NOT NULL,
                    min_inter_device_gap_ms INTEGER NOT NULL DEFAULT 900000,
                    min_repeat_gap_ms INTEGER NOT NULL DEFAULT 72000000,
                    created_at_ms INTEGER NOT NULL,
                    FOREIGN KEY(pool_id) REFERENCES target_pools(pool_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS round_device_seeds (
                    round_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    order_seed TEXT NOT NULL,
                    PRIMARY KEY(round_id, device_id),
                    UNIQUE(round_id, order_seed),
                    FOREIGN KEY(round_id) REFERENCES exposure_rounds(round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS round_assignments (
                    assignment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    order_key TEXT NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at_ms INTEGER NOT NULL DEFAULT 0,
                    visit_confirmed_at_ms INTEGER,
                    completed_at_ms INTEGER,
                    last_error_code TEXT,
                    lease_owner TEXT,
                    lease_expires_at_ms INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(round_id, identity_key, device_id),
                    UNIQUE(round_id, device_id, order_key),
                    FOREIGN KEY(round_id) REFERENCES exposure_rounds(round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS round_assignment_claim_idx
                ON round_assignments(
                    round_id, device_id, phase, next_attempt_at_ms, order_key
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS round_assignment_target_activity_idx
                ON round_assignments(
                    identity_key, lease_expires_at_ms, visit_confirmed_at_ms
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_control_states (
                    scope TEXT NOT NULL CHECK(scope IN ('device', 'assignment')),
                    scope_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('running', 'paused', 'stopped')),
                    updated_at_ms INTEGER NOT NULL,
                    command_id TEXT NOT NULL,
                    PRIMARY KEY(scope, scope_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_snapshot_leases (
                    round_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    owner_device_id TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(round_id, identity_key),
                    FOREIGN KEY(round_id) REFERENCES exposure_rounds(round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_snapshots (
                    round_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    observed_by_device_id TEXT NOT NULL,
                    observed_username TEXT NOT NULL,
                    following_count INTEGER,
                    followers_count INTEGER,
                    post_count INTEGER,
                    private_account INTEGER NOT NULL DEFAULT 0,
                    access_state TEXT NOT NULL DEFAULT 'public',
                    eligible INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    observed_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(round_id, identity_key),
                    FOREIGN KEY(round_id) REFERENCES exposure_rounds(round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_action_plans (
                    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    seed TEXT NOT NULL,
                    requested_outcome TEXT NOT NULL,
                    effective_outcome TEXT NOT NULL,
                    quota_window_start_ms INTEGER,
                    quota_reason TEXT,
                    video_key TEXT,
                    state TEXT NOT NULL DEFAULT 'planned',
                    created_at_ms INTEGER NOT NULL,
                    UNIQUE(round_id, identity_key, device_id),
                    FOREIGN KEY(round_id) REFERENCES exposure_rounds(round_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS device_action_plans_capacity_quota_idx
                ON device_action_plans(
                    device_id, effective_outcome, quota_window_start_ms, state
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS acquisition_quota_windows (
                    device_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    window_start_ms INTEGER NOT NULL,
                    reserved_count INTEGER NOT NULL DEFAULT 0,
                    confirmed_count INTEGER NOT NULL DEFAULT 0,
                    uncertain_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(device_id, outcome, window_start_ms)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_pacing_state (
                    device_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    tokens REAL NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(device_id, outcome)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assignment_phase_history (
                    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    assignment_id INTEGER NOT NULL,
                    from_phase TEXT NOT NULL,
                    to_phase TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    changed_at_ms INTEGER NOT NULL,
                    FOREIGN KEY(assignment_id) REFERENCES round_assignments(assignment_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assignment_stage_timings (
                    assignment_id INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
                    recorded_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(assignment_id, stage),
                    FOREIGN KEY(assignment_id)
                        REFERENCES round_assignments(assignment_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS assignment_phase_history_capacity_idx
                ON assignment_phase_history(
                    assignment_id, to_phase, changed_at_ms
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS action_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id INTEGER NOT NULL,
                    attempt_index INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL DEFAULT '{}',
                    attempted_at_ms INTEGER NOT NULL,
                    UNIQUE(plan_id, attempt_index),
                    FOREIGN KEY(plan_id) REFERENCES device_action_plans(plan_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_worker_leases (
                    device_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL UNIQUE,
                    owner_id TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            lease_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(device_worker_leases)"
                ).fetchall()
            }
            if "fence_token" not in lease_columns:
                connection.execute(
                    """
                    ALTER TABLE device_worker_leases
                    ADD COLUMN fence_token INTEGER NOT NULL DEFAULT 0
                    """
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_worker_fence_counters (
                    device_id TEXT PRIMARY KEY,
                    last_token INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS fleet_device_health (
                    device_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state IN ('starting', 'healthy', 'unhealthy', 'stopped')
                    ),
                    owner_id TEXT,
                    fence_token INTEGER NOT NULL,
                    process_id INTEGER,
                    error_code TEXT,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            health_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(fleet_device_health)"
                ).fetchall()
            }
            if "fence_token" not in health_columns:
                connection.execute(
                    """
                    ALTER TABLE fleet_device_health
                    ADD COLUMN fence_token INTEGER NOT NULL DEFAULT 0
                    """
                )

    def claim_device_worker_lease(
        self,
        device_id: str,
        account_id: str,
        owner_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> int | None:
        device_id, account_id, owner_id = self._worker_lease_identifiers(
            device_id, account_id, owner_id
        )
        if ttl_ms <= 0:
            raise ValueError("device worker lease TTL must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                DELETE FROM device_worker_leases
                WHERE expires_at_ms <= ?
                  AND (device_id = ? OR account_id = ?)
                """,
                (now_ms, device_id, account_id),
            )
            conflict = connection.execute(
                """
                SELECT 1 FROM device_worker_leases
                WHERE device_id = ? OR account_id = ?
                """,
                (device_id, account_id),
            ).fetchone()
            if conflict is not None:
                return None
            connection.execute(
                """
                INSERT INTO device_worker_fence_counters(device_id, last_token)
                VALUES (?, 1)
                ON CONFLICT(device_id) DO UPDATE SET
                    last_token = last_token + 1
                """,
                (device_id,),
            )
            counter = connection.execute(
                """
                SELECT last_token FROM device_worker_fence_counters
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            fence_token = int(counter["last_token"])
            connection.execute(
                """
                INSERT INTO device_worker_leases(
                    device_id, account_id, owner_id, fence_token,
                    expires_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    account_id,
                    owner_id,
                    fence_token,
                    now_ms + ttl_ms,
                    now_ms,
                ),
            )
        return fence_token

    def renew_device_worker_lease(
        self,
        device_id: str,
        account_id: str,
        owner_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
        fence_token: int,
    ) -> int:
        device_id, account_id, owner_id = self._worker_lease_identifiers(
            device_id, account_id, owner_id
        )
        if ttl_ms <= 0:
            raise ValueError("device worker lease TTL must be positive")
        expires_at_ms = now_ms + ttl_ms
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE device_worker_leases
                SET expires_at_ms = ?, updated_at_ms = ?
                WHERE device_id = ? AND account_id = ? AND owner_id = ?
                  AND expires_at_ms > ? AND fence_token = ?
                """,
                (
                    expires_at_ms,
                    now_ms,
                    device_id,
                    account_id,
                    owner_id,
                    now_ms,
                    int(fence_token),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("device worker lease is not active for renewal")
        return expires_at_ms

    def release_device_worker_lease(
        self,
        device_id: str,
        account_id: str,
        owner_id: str,
        *,
        fence_token: int,
    ) -> None:
        device_id, account_id, owner_id = self._worker_lease_identifiers(
            device_id, account_id, owner_id
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM device_worker_leases
                WHERE device_id = ? AND account_id = ? AND owner_id = ?
                  AND fence_token = ?
                """,
                (device_id, account_id, owner_id, int(fence_token)),
            )
            if cursor.rowcount != 1:
                raise ValueError("device worker lease owner does not match")

    def device_worker_lease(self, device_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM device_worker_leases WHERE device_id = ?",
                (str(device_id).strip(),),
            ).fetchone()
        return None if row is None else dict(row)

    def device_worker_fence_is_active(
        self,
        device_id: str,
        account_id: str,
        owner_id: str,
        fence_token: int,
        *,
        now_ms: int,
    ) -> bool:
        device_id, account_id, owner_id = self._worker_lease_identifiers(
            device_id, account_id, owner_id
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM device_worker_leases
                WHERE device_id = ? AND account_id = ? AND owner_id = ?
                  AND fence_token = ? AND expires_at_ms > ?
                """,
                (device_id, account_id, owner_id, int(fence_token), now_ms),
            ).fetchone()
        return row is not None

    @staticmethod
    def _assert_device_worker_fence(
        connection: sqlite3.Connection,
        *,
        device_id: str,
        account_id: str | None,
        owner_id: str | None,
        fence_token: int | None,
        now_ms: int,
    ) -> None:
        values = (account_id, owner_id, fence_token)
        if all(value is None for value in values):
            return
        if any(value is None for value in values):
            raise ValueError("device worker fence identity is incomplete")
        row = connection.execute(
            """
            SELECT 1 FROM device_worker_leases
            WHERE device_id = ? AND account_id = ? AND owner_id = ?
              AND fence_token = ? AND expires_at_ms > ?
            """,
            (
                device_id,
                str(account_id).strip(),
                str(owner_id).strip(),
                int(fence_token),
                now_ms,
            ),
        ).fetchone()
        if row is None:
            raise DeviceWorkerLeaseLost(
                f"device worker fence is inactive for {device_id}"
            )

    @staticmethod
    def _assert_assignment_lease_active(
        *,
        device_id: str,
        lease_expires_at_ms: int,
        now_ms: int,
        fenced: bool,
    ) -> None:
        if fenced and lease_expires_at_ms <= now_ms:
            raise DeviceWorkerLeaseLost(f"assignment lease is inactive for {device_id}")

    @classmethod
    def _assert_action_worker_fence(
        cls,
        connection: sqlite3.Connection,
        plan: ActionPlan,
        *,
        worker_owner_id: str | None,
        worker_account_id: str | None,
        worker_fence_token: int | None,
        now_ms: int,
    ) -> None:
        cls._assert_assignment_worker_fence(
            connection,
            round_id=plan.round_id,
            identity_key=plan.identity_key,
            device_id=plan.device_id,
            worker_owner_id=worker_owner_id,
            worker_account_id=worker_account_id,
            worker_fence_token=worker_fence_token,
            now_ms=now_ms,
        )

    @classmethod
    def _assert_assignment_worker_fence(
        cls,
        connection: sqlite3.Connection,
        *,
        round_id: str,
        identity_key: str,
        device_id: str,
        worker_owner_id: str | None,
        worker_account_id: str | None,
        worker_fence_token: int | None,
        now_ms: int,
    ) -> None:
        cls._assert_device_worker_fence(
            connection,
            device_id=device_id,
            account_id=worker_account_id,
            owner_id=worker_owner_id,
            fence_token=worker_fence_token,
            now_ms=now_ms,
        )
        if worker_fence_token is None:
            return
        assignment = connection.execute(
            """
            SELECT lease_expires_at_ms FROM round_assignments
            WHERE round_id = ? AND identity_key = ? AND device_id = ?
              AND lease_owner = ?
            """,
            (
                round_id,
                identity_key,
                device_id,
                worker_owner_id,
            ),
        ).fetchone()
        if assignment is None:
            raise DeviceWorkerLeaseLost(f"assignment lease is inactive for {device_id}")
        cls._assert_assignment_lease_active(
            device_id=device_id,
            lease_expires_at_ms=int(assignment["lease_expires_at_ms"]),
            now_ms=now_ms,
            fenced=True,
        )

    def record_fleet_device_health(
        self,
        device_id: str,
        account_id: str,
        state: str,
        *,
        now_ms: int,
        owner_id: str | None = None,
        process_id: int | None = None,
        error_code: str | None = None,
        fence_token: int,
        expected_owner_id: str | None = None,
        expected_fence_token: int | None = None,
        require_active_lease: bool = False,
    ) -> bool:
        device_id = str(device_id).strip()
        account_id = str(account_id).strip()
        normalized_state = str(state).strip()
        owner_id = None if owner_id is None else str(owner_id).strip() or None
        expected_owner_id = (
            None
            if expected_owner_id is None
            else str(expected_owner_id).strip() or None
        )
        if not device_id or not account_id:
            raise ValueError("fleet device and account identifiers are required")
        if normalized_state not in {"starting", "healthy", "unhealthy", "stopped"}:
            raise ValueError("fleet device health state is invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT owner_id, fence_token FROM fleet_device_health
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            active_lease = connection.execute(
                """
                SELECT owner_id, fence_token FROM device_worker_leases
                WHERE device_id = ? AND account_id = ? AND expires_at_ms > ?
                """,
                (device_id, account_id, now_ms),
            ).fetchone()
            active_owner = (
                None if active_lease is None else str(active_lease["owner_id"])
            )
            active_token = (
                None if active_lease is None else int(active_lease["fence_token"])
            )
            if require_active_lease and (
                owner_id is None
                or active_owner != owner_id
                or active_token != int(fence_token)
            ):
                return False
            if expected_owner_id is not None:
                current_owner = (
                    None
                    if current is None or current["owner_id"] is None
                    else str(current["owner_id"])
                )
                if current_owner != expected_owner_id:
                    return False
                current_token = None if current is None else int(current["fence_token"])
                if expected_fence_token is None or current_token != int(
                    expected_fence_token
                ):
                    return False
                if active_owner is not None and (
                    active_owner != expected_owner_id
                    or active_token != int(expected_fence_token)
                ):
                    return False
            connection.execute(
                """
                INSERT INTO fleet_device_health(
                    device_id, account_id, state, owner_id, fence_token,
                    process_id, error_code, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    account_id = excluded.account_id,
                    state = excluded.state,
                    owner_id = excluded.owner_id,
                    fence_token = excluded.fence_token,
                    process_id = excluded.process_id,
                    error_code = excluded.error_code,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    device_id,
                    account_id,
                    normalized_state,
                    owner_id,
                    int(fence_token),
                    process_id,
                    None if error_code is None else str(error_code).strip() or None,
                    now_ms,
                ),
            )
        return True

    def fleet_device_health(self, device_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM fleet_device_health WHERE device_id = ?",
                (str(device_id).strip(),),
            ).fetchone()
        return None if row is None else dict(row)

    @staticmethod
    def _worker_lease_identifiers(
        device_id: str, account_id: str, owner_id: str
    ) -> tuple[str, str, str]:
        values = tuple(
            str(value).strip() for value in (device_id, account_id, owner_id)
        )
        if any(not value for value in values):
            raise ValueError("device, account, and worker identifiers are required")
        return values

    def import_pool(
        self,
        source_name: str,
        source_checksum: str,
        targets: Sequence[Target],
    ) -> PoolImport:
        normalized_source_name = str(source_name).strip()
        checksum = str(source_checksum).strip()
        if not normalized_source_name:
            raise ValueError("source name is empty")
        if _SHA256_PATTERN.fullmatch(checksum) is None:
            raise ValueError("source checksum must be lowercase SHA-256")

        pool_id = f"pool-{checksum[:20]}"
        pool_targets = tuple(
            self._normalize_target(pool_id, target, ordinal)
            for ordinal, target in enumerate(targets)
        )
        if not pool_targets:
            raise ValueError("target pool is empty")
        identities = [target.identity_key for target in pool_targets]
        if len(set(identities)) != len(identities):
            raise ValueError("target pool contains duplicate identities")
        source_rows = sum(
            max(1, len(target.source_line_numbers)) for target in pool_targets
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM target_pools WHERE source_checksum = ?",
                (checksum,),
            ).fetchone()
            if existing is not None:
                stored_targets = self._pool_targets(
                    connection, str(existing["pool_id"])
                )
                if self._content_signature(stored_targets) != self._content_signature(
                    pool_targets
                ):
                    raise ValueError("checksum already has different content")
                return PoolImport(
                    pool_id=str(existing["pool_id"]),
                    unique_targets=int(existing["unique_targets"]),
                    source_rows=int(existing["source_rows"]),
                )

            connection.execute(
                """
                INSERT INTO target_pools(
                    pool_id, source_name, source_checksum,
                    unique_targets, source_rows, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_id,
                    normalized_source_name,
                    checksum,
                    len(pool_targets),
                    source_rows,
                    int(self.clock_ms()),
                ),
            )
            connection.executemany(
                """
                INSERT INTO pool_targets(
                    pool_id, identity_key, target_id, sec_uid, username,
                    profile_url, source_video_id, source_line_numbers_json, ordinal
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        target.pool_id,
                        target.identity_key,
                        target.target_id,
                        target.sec_uid,
                        target.username,
                        target.profile_url,
                        target.source_video_id,
                        json.dumps(target.source_line_numbers),
                        target.ordinal,
                    )
                    for target in pool_targets
                ],
            )
        return PoolImport(pool_id, len(pool_targets), source_rows)

    def pool_targets(self, pool_id: str) -> tuple[PoolTarget, ...]:
        with self._connect() as connection:
            return self._pool_targets(connection, pool_id)

    def pool_exists(self, pool_id: str) -> bool:
        normalized_pool_id = str(pool_id).strip()
        if not normalized_pool_id:
            return False
        with self._connect_read_only() as connection:
            if not self._table_exists(connection, "pool_targets"):
                return False
            return (
                connection.execute(
                    "SELECT 1 FROM pool_targets WHERE pool_id = ? LIMIT 1",
                    (normalized_pool_id,),
                ).fetchone()
                is not None
            )

    def create_round(
        self,
        *,
        round_id: str,
        pool_id: str,
        device_seeds: Mapping[str, str],
        starts_at_ms: int,
        min_inter_device_gap_ms: int,
        min_repeat_gap_ms: int,
        order_keys: Mapping[tuple[str, str], str],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM exposure_rounds WHERE round_id = ?", (round_id,)
            ).fetchone()
            if existing is not None:
                stored_seeds = {
                    str(row["device_id"]): str(row["order_seed"])
                    for row in connection.execute(
                        "SELECT device_id, order_seed FROM round_device_seeds WHERE round_id = ?",
                        (round_id,),
                    )
                }
                stored_orders = {
                    (str(row["identity_key"]), str(row["device_id"])): str(
                        row["order_key"]
                    )
                    for row in connection.execute(
                        "SELECT identity_key, device_id, order_key FROM round_assignments WHERE round_id = ?",
                        (round_id,),
                    )
                }
                same_round = (
                    str(existing["pool_id"]) == pool_id
                    and int(existing["starts_at_ms"]) == starts_at_ms
                    and int(existing["min_inter_device_gap_ms"])
                    == min_inter_device_gap_ms
                    and int(existing["min_repeat_gap_ms"]) == min_repeat_gap_ms
                    and stored_seeds == dict(device_seeds)
                    and stored_orders == dict(order_keys)
                )
                if not same_round:
                    raise ValueError("round id already has different content")
                return

            pool_targets = self._pool_targets(connection, pool_id)
            if not pool_targets:
                raise ValueError("target pool does not exist or is empty")
            expected_order_keys = {
                (target.identity_key, device_id)
                for target in pool_targets
                for device_id in device_seeds
            }
            if set(order_keys) != expected_order_keys:
                raise ValueError("round order keys do not cover every assignment")

            active = connection.execute(
                """
                SELECT round_id FROM exposure_rounds
                WHERE pool_id = ? AND state IN ('pending', 'running', 'paused')
                LIMIT 1
                """,
                (pool_id,),
            ).fetchone()
            if active is not None:
                raise ValueError("target pool already has an active round")
            previous = connection.execute(
                """
                SELECT starts_at_ms, min_repeat_gap_ms
                FROM exposure_rounds
                WHERE pool_id = ?
                ORDER BY starts_at_ms DESC
                LIMIT 1
                """,
                (pool_id,),
            ).fetchone()
            if previous is not None:
                earliest = int(previous["starts_at_ms"]) + max(
                    int(previous["min_repeat_gap_ms"]), min_repeat_gap_ms
                )
                if starts_at_ms < earliest:
                    raise ValueError("round starts before the repeat gap")

            connection.execute(
                """
                INSERT INTO exposure_rounds(
                    round_id, pool_id, state, starts_at_ms,
                    min_inter_device_gap_ms, min_repeat_gap_ms, created_at_ms
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    round_id,
                    pool_id,
                    starts_at_ms,
                    min_inter_device_gap_ms,
                    min_repeat_gap_ms,
                    int(self.clock_ms()),
                ),
            )
            connection.executemany(
                """
                INSERT INTO round_device_seeds(round_id, device_id, order_seed)
                VALUES (?, ?, ?)
                """,
                [
                    (round_id, device_id, seed)
                    for device_id, seed in sorted(device_seeds.items())
                ],
            )
            connection.executemany(
                """
                INSERT INTO round_assignments(
                    round_id, identity_key, device_id, order_key
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        round_id,
                        identity_key,
                        device_id,
                        order_keys[(identity_key, device_id)],
                    )
                    for identity_key, device_id in sorted(expected_order_keys)
                ],
            )

    def assignment_count(self, round_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM round_assignments WHERE round_id = ?",
                (round_id,),
            ).fetchone()
            return int(row["count"])

    def device_target_order(self, round_id: str, device_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT identity_key FROM round_assignments
                WHERE round_id = ? AND device_id = ?
                ORDER BY order_key
                """,
                (round_id, device_id),
            ).fetchall()
            return tuple(str(row["identity_key"]) for row in rows)

    def round_device_ids(self, round_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT 1 FROM exposure_rounds WHERE round_id = ?", (round_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(round_id)
            return tuple(
                str(row["device_id"])
                for row in connection.execute(
                    """
                    SELECT device_id FROM round_device_seeds
                    WHERE round_id = ? ORDER BY device_id
                    """,
                    (round_id,),
                )
            )

    def round_exists(self, round_id: str) -> bool:
        normalized_round_id = str(round_id).strip()
        if not normalized_round_id:
            return False
        with self._connect_read_only() as connection:
            if not self._table_exists(connection, "exposure_rounds"):
                return False
            return (
                connection.execute(
                    "SELECT 1 FROM exposure_rounds WHERE round_id = ? LIMIT 1",
                    (normalized_round_id,),
                ).fetchone()
                is not None
            )

    def claim_next_assignment(
        self,
        round_id: str,
        device_id: str,
        owner_id: str,
        *,
        now_ms: int,
        lease_ttl_ms: int = 120_000,
    ) -> RoundAssignment | None:
        if not owner_id.strip() or lease_ttl_ms <= 0:
            raise ValueError("assignment owner and lease TTL must be valid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT assignment.assignment_id, assignment.phase
                FROM round_assignments AS assignment
                JOIN exposure_rounds AS round
                  ON round.round_id = assignment.round_id
                WHERE assignment.round_id = ?
                  AND assignment.device_id = ?
                  AND assignment.phase IN ('pending', 'deferred')
                  AND assignment.next_attempt_at_ms <= ?
                  AND round.state IN ('pending', 'running')
                  AND round.starts_at_ms <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM operator_control_states AS device_control
                      WHERE device_control.scope = 'device'
                        AND device_control.scope_id = assignment.device_id
                        AND device_control.state IN ('paused', 'stopped')
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM operator_control_states AS assignment_control
                      WHERE assignment_control.scope = 'assignment'
                        AND assignment_control.scope_id = CAST(
                            assignment.assignment_id AS TEXT
                        )
                        AND assignment_control.state IN ('paused', 'stopped')
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM round_assignments AS blocker
                      WHERE blocker.identity_key = assignment.identity_key
                        AND blocker.assignment_id <> assignment.assignment_id
                        AND (
                            (
                                blocker.lease_owner IS NOT NULL
                                AND blocker.lease_expires_at_ms > ?
                            )
                            OR (
                                blocker.visit_confirmed_at_ms IS NOT NULL
                                AND blocker.visit_confirmed_at_ms >
                                    (? - round.min_inter_device_gap_ms)
                            )
                        )
                  )
                ORDER BY
                    CASE assignment.phase WHEN 'deferred' THEN 0 ELSE 1 END,
                    assignment.order_key
                LIMIT 1
                """,
                (round_id, device_id, now_ms, now_ms, now_ms, now_ms),
            ).fetchone()
            if row is None:
                return None
            assignment_id = int(row["assignment_id"])
            previous_phase = AssignmentPhase(str(row["phase"]))
            connection.execute(
                """
                UPDATE round_assignments
                SET phase = 'profile_opening',
                    attempt_count = attempt_count + 1,
                    lease_owner = ?,
                    lease_expires_at_ms = ?
                WHERE assignment_id = ?
                """,
                (owner_id, now_ms + lease_ttl_ms, assignment_id),
            )
            self._insert_phase_history(
                connection,
                assignment_id,
                previous_phase,
                AssignmentPhase.PROFILE_OPENING,
                now_ms,
                {"owner_id": owner_id},
            )
            connection.execute(
                """
                UPDATE exposure_rounds SET state = 'running'
                WHERE round_id = ? AND state = 'pending'
                """,
                (round_id,),
            )
            return self._assignment_by_id(connection, assignment_id)

    def record_visit_confirmed(
        self,
        assignment_id: int,
        owner_id: str,
        *,
        now_ms: int,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT device_id, phase, lease_expires_at_ms
                FROM round_assignments
                WHERE assignment_id = ? AND lease_owner = ?
                """,
                (assignment_id, owner_id),
            ).fetchone()
            if row is None:
                raise ValueError("assignment visit owner does not hold the lease")
            self._assert_device_worker_fence(
                connection,
                device_id=str(row["device_id"]),
                account_id=worker_account_id,
                owner_id=owner_id if worker_fence_token is not None else None,
                fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            self._assert_assignment_lease_active(
                device_id=str(row["device_id"]),
                lease_expires_at_ms=int(row["lease_expires_at_ms"]),
                now_ms=now_ms,
                fenced=worker_fence_token is not None,
            )
            previous_phase = AssignmentPhase(str(row["phase"]))
            if AssignmentPhase.IDENTITY_CONFIRMED not in _ALLOWED_PHASE_TRANSITIONS.get(
                previous_phase, set()
            ):
                raise ValueError("assignment phase cannot confirm profile identity")
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET visit_confirmed_at_ms = COALESCE(visit_confirmed_at_ms, ?),
                    phase = 'identity_confirmed'
                WHERE assignment_id = ? AND lease_owner = ?
                """,
                (now_ms, assignment_id, owner_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment visit owner does not hold the lease")
            self._insert_phase_history(
                connection,
                assignment_id,
                previous_phase,
                AssignmentPhase.IDENTITY_CONFIRMED,
                now_ms,
                {},
            )

    def release_assignment_lease(self, assignment_id: int, owner_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET lease_owner = NULL, lease_expires_at_ms = 0
                WHERE assignment_id = ? AND lease_owner = ?
                """,
                (assignment_id, owner_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment owner does not hold the lease")

    def renew_assignment_lease(
        self,
        assignment_id: int,
        owner_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> int:
        if ttl_ms <= 0:
            raise ValueError("assignment lease TTL must be positive")
        expires_at_ms = now_ms + ttl_ms
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET lease_expires_at_ms = ?
                WHERE assignment_id = ? AND lease_owner = ?
                  AND lease_expires_at_ms > ?
                  AND phase NOT IN ('completed', 'skipped')
                """,
                (expires_at_ms, assignment_id, owner_id, now_ms),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment lease is not active for renewal")
        return expires_at_ms

    def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT assignment_id, phase, visit_confirmed_at_ms,
                       next_attempt_at_ms
                FROM round_assignments
                WHERE lease_owner IS NOT NULL
                  AND lease_expires_at_ms <= ?
                  AND phase NOT IN ('completed', 'skipped')
                """,
                (now_ms,),
            ).fetchall()
            for row in rows:
                assignment_id = int(row["assignment_id"])
                previous = AssignmentPhase(str(row["phase"]))
                next_phase = (
                    AssignmentPhase.PENDING
                    if row["visit_confirmed_at_ms"] is None
                    else AssignmentPhase.DEFERRED
                )
                next_attempt_at_ms = max(int(row["next_attempt_at_ms"]), now_ms)
                connection.execute(
                    """
                    UPDATE round_assignments
                    SET phase = ?, lease_owner = NULL,
                        lease_expires_at_ms = 0, next_attempt_at_ms = ?
                    WHERE assignment_id = ?
                    """,
                    (next_phase.value, next_attempt_at_ms, assignment_id),
                )
                self._insert_phase_history(
                    connection,
                    assignment_id,
                    previous,
                    next_phase,
                    now_ms,
                    {"reason": "lease_expired"},
                )
            return len(rows)

    def assignment(self, assignment_id: int) -> RoundAssignment:
        with self._connect() as connection:
            return self._assignment_by_id(connection, assignment_id)

    def assignment_exists(self, assignment_id: int) -> bool:
        if assignment_id <= 0:
            return False
        with self._connect_read_only() as connection:
            if not self._table_exists(connection, "round_assignments"):
                return False
            return (
                connection.execute(
                    """
                    SELECT 1 FROM round_assignments
                    WHERE assignment_id = ? LIMIT 1
                    """,
                    (assignment_id,),
                ).fetchone()
                is not None
            )

    def assignment_phase_history(
        self, assignment_id: int
    ) -> tuple[AssignmentTransition, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM assignment_phase_history
                WHERE assignment_id = ?
                ORDER BY history_id
                """,
                (assignment_id,),
            ).fetchall()
            return tuple(
                AssignmentTransition(
                    history_id=int(row["history_id"]),
                    assignment_id=int(row["assignment_id"]),
                    from_phase=AssignmentPhase(str(row["from_phase"])),
                    to_phase=AssignmentPhase(str(row["to_phase"])),
                    details=dict(json.loads(row["details_json"])),
                    changed_at_ms=int(row["changed_at_ms"]),
                )
                for row in rows
            )

    def record_assignment_stage_timing(
        self,
        assignment_id: int,
        stage: AssignmentStage | str,
        *,
        duration_ms: int,
        recorded_at_ms: int,
    ) -> AssignmentStageTiming:
        normalized = AssignmentStage(stage)
        if assignment_id <= 0:
            raise ValueError("assignment id must be positive")
        if duration_ms < 0 or recorded_at_ms < 0:
            raise ValueError("assignment stage timing must be nonnegative")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO assignment_stage_timings(
                    assignment_id, stage, duration_ms, recorded_at_ms
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(assignment_id, stage) DO UPDATE SET
                    duration_ms=excluded.duration_ms,
                    recorded_at_ms=excluded.recorded_at_ms
                """,
                (
                    assignment_id,
                    normalized.value,
                    duration_ms,
                    recorded_at_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment stage timing was not recorded")
        return AssignmentStageTiming(
            assignment_id=assignment_id,
            stage=normalized,
            duration_ms=duration_ms,
            recorded_at_ms=recorded_at_ms,
        )

    def assignment_stage_timings(
        self, assignment_id: int
    ) -> tuple[AssignmentStageTiming, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT assignment_id, stage, duration_ms, recorded_at_ms
                FROM assignment_stage_timings
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchall()
        timings = tuple(
            AssignmentStageTiming(
                assignment_id=int(row["assignment_id"]),
                stage=AssignmentStage(str(row["stage"])),
                duration_ms=int(row["duration_ms"]),
                recorded_at_ms=int(row["recorded_at_ms"]),
            )
            for row in rows
        )
        order = {stage: index for index, stage in enumerate(AssignmentStage)}
        return tuple(sorted(timings, key=lambda timing: order[timing.stage]))

    def transition_assignment(
        self,
        assignment_id: int,
        owner_id: str,
        expected_phase: AssignmentPhase | str,
        next_phase: AssignmentPhase | str,
        *,
        now_ms: int,
        details: Mapping[str, object] | None = None,
    ) -> RoundAssignment:
        expected = AssignmentPhase(expected_phase)
        next_value = AssignmentPhase(next_phase)
        if next_value not in _ALLOWED_PHASE_TRANSITIONS.get(expected, set()):
            raise ValueError(f"invalid assignment transition: {expected}->{next_value}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET phase = ?
                WHERE assignment_id = ? AND lease_owner = ? AND phase = ?
                """,
                (next_value.value, assignment_id, owner_id, expected.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment phase or lease owner changed")
            self._insert_phase_history(
                connection,
                assignment_id,
                expected,
                next_value,
                now_ms,
                details or {},
            )
            return self._assignment_by_id(connection, assignment_id)

    def defer_assignment(
        self,
        assignment_id: int,
        owner_id: str,
        *,
        now_ms: int,
        retry_delay_ms: int,
        error_code: str,
        diagnostics: DeviceDiagnostics,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> RoundAssignment:
        if retry_delay_ms < 0:
            raise ValueError("retry delay must be nonnegative")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT device_id, phase, lease_expires_at_ms
                FROM round_assignments
                WHERE assignment_id = ? AND lease_owner = ?
                """,
                (assignment_id, owner_id),
            ).fetchone()
            if row is None:
                raise ValueError("assignment owner does not hold the lease")
            self._assert_device_worker_fence(
                connection,
                device_id=str(row["device_id"]),
                account_id=worker_account_id,
                owner_id=owner_id if worker_fence_token is not None else None,
                fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            self._assert_assignment_lease_active(
                device_id=str(row["device_id"]),
                lease_expires_at_ms=int(row["lease_expires_at_ms"]),
                now_ms=now_ms,
                fenced=worker_fence_token is not None,
            )
            previous = AssignmentPhase(str(row["phase"]))
            if previous in {AssignmentPhase.COMPLETED, AssignmentPhase.SKIPPED}:
                raise ValueError("terminal assignment cannot be deferred")
            connection.execute(
                """
                UPDATE round_assignments
                SET phase = 'deferred', next_attempt_at_ms = ?,
                    last_error_code = ?, lease_owner = NULL,
                    lease_expires_at_ms = 0
                WHERE assignment_id = ?
                """,
                (now_ms + retry_delay_ms, error_code, assignment_id),
            )
            self._insert_phase_history(
                connection,
                assignment_id,
                previous,
                AssignmentPhase.DEFERRED,
                now_ms,
                {
                    "error_code": error_code,
                    "screenshot_path": diagnostics.screenshot_path,
                    "ui_summary": diagnostics.ui_summary,
                },
            )
            return self._assignment_by_id(connection, assignment_id)

    def skip_unreachable_assignment(
        self,
        assignment_id: int,
        owner_id: str,
        *,
        now_ms: int,
        error_code: str,
        original_error_code: str,
        failure_stage: str,
        diagnostics: DeviceDiagnostics,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> RoundAssignment:
        normalized_error = str(error_code).strip()
        normalized_original_error = str(original_error_code).strip()
        normalized_stage = str(failure_stage).strip()
        if not normalized_error or not normalized_original_error:
            raise ValueError("skip error codes must be nonempty")
        if normalized_stage not in {"route", "identity"}:
            raise ValueError("skip failure stage must be route or identity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT round_id, device_id, phase, attempt_count,
                       visit_confirmed_at_ms, lease_expires_at_ms
                FROM round_assignments
                WHERE assignment_id = ? AND lease_owner = ?
                """,
                (assignment_id, owner_id),
            ).fetchone()
            if row is None:
                raise ValueError("assignment owner does not hold the lease")
            self._assert_device_worker_fence(
                connection,
                device_id=str(row["device_id"]),
                account_id=worker_account_id,
                owner_id=owner_id if worker_fence_token is not None else None,
                fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            self._assert_assignment_lease_active(
                device_id=str(row["device_id"]),
                lease_expires_at_ms=int(row["lease_expires_at_ms"]),
                now_ms=now_ms,
                fenced=worker_fence_token is not None,
            )
            if (
                AssignmentPhase(str(row["phase"]))
                is not AssignmentPhase.PROFILE_OPENING
            ):
                raise ValueError("only a profile-opening assignment can be skipped")
            if row["visit_confirmed_at_ms"] is not None:
                raise ValueError("confirmed visit cannot be skipped as unreachable")
            attempt_count = int(row["attempt_count"])
            if attempt_count < 3:
                raise ValueError("unreachable assignment requires three attempts")
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET phase = 'skipped', completed_at_ms = ?, last_error_code = ?,
                    lease_owner = NULL, lease_expires_at_ms = 0
                WHERE assignment_id = ? AND lease_owner = ?
                  AND phase = 'profile_opening'
                  AND visit_confirmed_at_ms IS NULL
                """,
                (now_ms, normalized_error, assignment_id, owner_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment phase or lease owner changed")
            self._insert_phase_history(
                connection,
                assignment_id,
                AssignmentPhase.PROFILE_OPENING,
                AssignmentPhase.SKIPPED,
                now_ms,
                {
                    "attempt_count": attempt_count,
                    "error_code": normalized_error,
                    "failure_stage": normalized_stage,
                    "original_error_code": normalized_original_error,
                    "screenshot_path": diagnostics.screenshot_path,
                    "ui_summary": diagnostics.ui_summary,
                },
            )
            round_id = str(row["round_id"])
            self._mark_round_completed_if_terminal(connection, round_id)
            return self._assignment_by_id(connection, assignment_id)

    def complete_assignment(
        self,
        assignment_id: int,
        owner_id: str,
        expected_phase: AssignmentPhase | str,
        *,
        now_ms: int,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> RoundAssignment:
        expected = AssignmentPhase(expected_phase)
        if AssignmentPhase.COMPLETED not in _ALLOWED_PHASE_TRANSITIONS.get(
            expected, set()
        ):
            raise ValueError("assignment phase cannot complete")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT round_id, device_id, lease_expires_at_ms "
                "FROM round_assignments "
                "WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(assignment_id)
            self._assert_device_worker_fence(
                connection,
                device_id=str(row["device_id"]),
                account_id=worker_account_id,
                owner_id=owner_id if worker_fence_token is not None else None,
                fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            self._assert_assignment_lease_active(
                device_id=str(row["device_id"]),
                lease_expires_at_ms=int(row["lease_expires_at_ms"]),
                now_ms=now_ms,
                fenced=worker_fence_token is not None,
            )
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET phase = 'completed', completed_at_ms = ?,
                    last_error_code = NULL, lease_owner = NULL,
                    lease_expires_at_ms = 0
                WHERE assignment_id = ? AND lease_owner = ? AND phase = ?
                """,
                (now_ms, assignment_id, owner_id, expected.value),
            )
            if cursor.rowcount != 1:
                raise ValueError("assignment phase or lease owner changed")
            self._insert_phase_history(
                connection,
                assignment_id,
                expected,
                AssignmentPhase.COMPLETED,
                now_ms,
                {},
            )
            round_id = str(row["round_id"])
            self._mark_round_completed_if_terminal(connection, round_id)
            return self._assignment_by_id(connection, assignment_id)

    def round_completion(self, round_id: str) -> RoundCompletion:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN visit_confirmed_at_ms IS NOT NULL THEN 1 ELSE 0 END) AS visits,
                       SUM(CASE WHEN phase = 'completed' THEN 1 ELSE 0 END) AS completed,
                       SUM(CASE WHEN phase = 'deferred' THEN 1 ELSE 0 END) AS deferred,
                       SUM(CASE WHEN phase = 'skipped' THEN 1 ELSE 0 END) AS skipped
                FROM round_assignments
                WHERE round_id = ?
                """,
                (round_id,),
            ).fetchone()
            return RoundCompletion(
                total=int(row["total"] or 0),
                visits_confirmed=int(row["visits"] or 0),
                completed=int(row["completed"] or 0),
                deferred=int(row["deferred"] or 0),
                skipped=int(row["skipped"] or 0),
            )

    def round_coverage(self, round_id: str) -> dict[str, object]:
        normalized_round_id = str(round_id).strip()
        if not normalized_round_id:
            raise ValueError("round id must be nonempty")
        with self._connect_read_only() as connection:
            if not self._table_exists(connection, "round_assignments"):
                raise KeyError(normalized_round_id)
            existing = connection.execute(
                "SELECT 1 FROM exposure_rounds WHERE round_id = ?",
                (normalized_round_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(normalized_round_id)
            required_devices = int(
                connection.execute(
                    "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                    (normalized_round_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT identity_key,
                       SUM(visit_confirmed_at_ms IS NOT NULL) AS confirmed_devices
                FROM round_assignments
                WHERE round_id = ?
                GROUP BY identity_key
                """,
                (normalized_round_id,),
            ).fetchall()
        targets = len(rows)
        confirmed_visits = sum(int(row["confirmed_devices"]) for row in rows)
        fully_covered = sum(
            int(row["confirmed_devices"]) == required_devices for row in rows
        )
        return {
            "round_id": normalized_round_id,
            "targets": targets,
            "required_devices": required_devices,
            "confirmed_visits": confirmed_visits,
            "fully_covered": fully_covered,
            "coverage_rate": 0.0 if targets == 0 else fully_covered / targets,
        }

    def recent_mobile_traces(
        self, round_id: str, *, limit: int = 100
    ) -> list[dict[str, object]]:
        normalized_round_id = str(round_id).strip()
        if not normalized_round_id:
            raise ValueError("round id must be nonempty")
        bounded_limit = max(1, min(int(limit), 500))
        with self._connect_read_only() as connection:
            existing = connection.execute(
                "SELECT 1 FROM exposure_rounds WHERE round_id = ?",
                (normalized_round_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(normalized_round_id)
            required_devices = int(
                connection.execute(
                    "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                    (normalized_round_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT assignment.identity_key, target.username,
                       SUM(assignment.visit_confirmed_at_ms IS NOT NULL)
                           AS confirmed_devices,
                       MAX(assignment.visit_confirmed_at_ms)
                           AS last_visit_confirmed_at_ms
                FROM round_assignments AS assignment
                JOIN exposure_rounds AS round
                  ON round.round_id = assignment.round_id
                JOIN pool_targets AS target
                  ON target.pool_id = round.pool_id
                 AND target.identity_key = assignment.identity_key
                WHERE assignment.round_id = ?
                GROUP BY assignment.identity_key, target.username
                HAVING confirmed_devices > 0
                ORDER BY last_visit_confirmed_at_ms DESC, assignment.identity_key
                LIMIT ?
                """,
                (normalized_round_id, bounded_limit),
            ).fetchall()
        return [
            {
                "identity_key": str(row["identity_key"]),
                "username": str(row["username"]),
                "confirmed_devices": int(row["confirmed_devices"]),
                "required_devices": required_devices,
                "fully_covered": int(row["confirmed_devices"]) == required_devices,
                "last_visit_confirmed_at_ms": int(row["last_visit_confirmed_at_ms"]),
            }
            for row in rows
        ]

    def retry_assignment(self, assignment_id: int) -> RoundAssignment:
        if assignment_id <= 0:
            raise ValueError("assignment id must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT phase, lease_owner FROM round_assignments
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(assignment_id)
            if AssignmentPhase(str(row["phase"])) is not AssignmentPhase.DEFERRED:
                raise ValueError("assignment is not deferred")
            if row["lease_owner"] is not None:
                raise ValueError("deferred assignment still has an active owner")
            connection.execute(
                """
                UPDATE round_assignments SET next_attempt_at_ms = 0
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            )
            return self._assignment_by_id(connection, assignment_id)

    def capacity_audit(
        self, round_id: str, *, expected_devices: int
    ) -> RoundCapacityAudit:
        if expected_devices <= 0:
            raise ValueError("expected device count must be positive")
        with self._connect_read_only() as connection:
            connection.execute("BEGIN")
            existing = connection.execute(
                "SELECT 1 FROM exposure_rounds WHERE round_id = ?", (round_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(round_id)
            device_ids = tuple(
                str(row["device_id"])
                for row in connection.execute(
                    """
                    SELECT device_id FROM round_device_seeds
                    WHERE round_id = ? ORDER BY device_id
                    """,
                    (round_id,),
                )
            )
            assignment_rows = connection.execute(
                """
                SELECT assignment_id, identity_key, device_id, phase,
                       visit_confirmed_at_ms, completed_at_ms
                FROM round_assignments
                WHERE round_id = ?
                ORDER BY assignment_id
                """,
                (round_id,),
            ).fetchall()
            final_claims = {
                int(row["assignment_id"]): int(row["started_at_ms"])
                for row in connection.execute(
                    """
                    SELECT history.assignment_id,
                           MAX(history.changed_at_ms) AS started_at_ms
                    FROM assignment_phase_history AS history
                    JOIN round_assignments AS assignment
                      ON assignment.assignment_id = history.assignment_id
                    WHERE assignment.round_id = ?
                      AND history.to_phase = 'profile_opening'
                    GROUP BY history.assignment_id
                    """,
                    (round_id,),
                )
            }
            video_confirmations = {
                int(row["assignment_id"]): int(row["confirmed_at_ms"])
                for row in connection.execute(
                    """
                    SELECT history.assignment_id,
                           MIN(history.changed_at_ms) AS confirmed_at_ms
                    FROM assignment_phase_history AS history
                    JOIN round_assignments AS assignment
                      ON assignment.assignment_id = history.assignment_id
                    WHERE assignment.round_id = ?
                      AND history.from_phase = 'video_opening'
                      AND history.to_phase = 'video_confirmed'
                    GROUP BY history.assignment_id
                    """,
                    (round_id,),
                )
            }
            confirmed_plans = {
                (str(row["identity_key"]), str(row["device_id"])): dict(row)
                for row in connection.execute(
                    """
                    SELECT plan.plan_id, plan.identity_key, plan.device_id,
                           plan.requested_outcome, plan.effective_outcome,
                           plan.created_at_ms, plan.video_key,
                           plan.quota_window_start_ms, plan.quota_reason,
                           CASE WHEN effective_quota.device_id IS NULL
                               THEN 0 ELSE 1 END AS effective_quota_matched,
                           requested_quota.reserved_count
                               AS requested_quota_reserved_count,
                           requested_quota.confirmed_count
                               AS requested_quota_confirmed_count,
                           requested_quota.uncertain_count
                               AS requested_quota_uncertain_count,
                           MIN(CASE WHEN attempt.result = 'confirmed'
                               THEN attempt.attempted_at_ms END) AS confirmed_at_ms
                          ,MIN(CASE WHEN attempt.result = 'unavailable'
                               THEN attempt.attempted_at_ms END) AS unavailable_at_ms
                    FROM device_action_plans AS plan
                    LEFT JOIN action_attempts AS attempt
                      ON attempt.plan_id = plan.plan_id
                    LEFT JOIN acquisition_quota_windows AS effective_quota
                      ON effective_quota.device_id = plan.device_id
                     AND effective_quota.outcome = plan.effective_outcome
                     AND effective_quota.window_start_ms =
                         plan.quota_window_start_ms
                    LEFT JOIN acquisition_quota_windows AS requested_quota
                      ON requested_quota.device_id = plan.device_id
                     AND requested_quota.outcome = plan.requested_outcome
                     AND requested_quota.window_start_ms =
                         plan.quota_window_start_ms
                    WHERE plan.round_id = ? AND plan.state = 'confirmed'
                    GROUP BY plan.plan_id
                    """,
                    (round_id,),
                )
            }
            snapshots = {
                str(row["identity_key"]): dict(row)
                for row in connection.execute(
                    """
                    SELECT snapshot.*, target.username AS expected_username,
                           EXISTS (
                               SELECT 1 FROM round_assignments AS observer
                               WHERE observer.round_id = snapshot.round_id
                                 AND observer.identity_key = snapshot.identity_key
                                 AND observer.device_id =
                                     snapshot.observed_by_device_id
                                 AND observer.visit_confirmed_at_ms IS NOT NULL
                                 AND observer.visit_confirmed_at_ms
                                     <= snapshot.observed_at_ms
                           ) AS observer_valid
                    FROM profile_snapshots AS snapshot
                    JOIN exposure_rounds AS round
                      ON round.round_id = snapshot.round_id
                    JOIN pool_targets AS target
                      ON target.pool_id = round.pool_id
                     AND target.identity_key = snapshot.identity_key
                    WHERE snapshot.round_id = ?
                    """,
                    (round_id,),
                )
            }

            timings: list[AssignmentTiming] = []
            invalid_outcome_plan_ids: set[int] = set()
            completed_count = 0
            for row in assignment_rows:
                if str(row["phase"]) != AssignmentPhase.COMPLETED.value:
                    continue
                completed_count += 1
                assignment_id = int(row["assignment_id"])
                started_at_ms = final_claims.get(assignment_id)
                completed_at_ms = row["completed_at_ms"]
                plan_key = (str(row["identity_key"]), str(row["device_id"]))
                plan_evidence = confirmed_plans.get(plan_key)
                snapshot_evidence = snapshots.get(plan_key[0])
                video_confirmed_at_ms = video_confirmations.get(assignment_id)
                visit_confirmed_at_ms = row["visit_confirmed_at_ms"]
                visit_value = (
                    None
                    if visit_confirmed_at_ms is None
                    else int(visit_confirmed_at_ms)
                )
                completed_value = (
                    None if completed_at_ms is None else int(completed_at_ms)
                )
                plan_valid = False
                if (
                    plan_evidence is not None
                    and snapshot_evidence is not None
                    and started_at_ms is not None
                    and completed_value is not None
                    and visit_value is not None
                ):
                    plan_id = int(plan_evidence["plan_id"])
                    plan_created_at_ms = int(plan_evidence["created_at_ms"])
                    video_key = str(plan_evidence["video_key"] or "").strip() or None
                    confirmed_at_ms = (
                        None
                        if plan_evidence["confirmed_at_ms"] is None
                        else int(plan_evidence["confirmed_at_ms"])
                    )
                    unavailable_at_ms = (
                        None
                        if plan_evidence["unavailable_at_ms"] is None
                        else int(plan_evidence["unavailable_at_ms"])
                    )
                    snapshot_eligible = int(snapshot_evidence["eligible"])
                    snapshot_observed_at_ms = int(snapshot_evidence["observed_at_ms"])
                    relationship_valid, effective_outcome = (
                        self._capacity_plan_relationship_is_valid(
                            plan_evidence,
                            snapshot_eligible=bool(snapshot_eligible),
                        )
                    )
                    if not relationship_valid:
                        invalid_outcome_plan_ids.add(plan_id)
                    snapshot_valid = (
                        self._capacity_snapshot_is_valid(snapshot_evidence)
                        and bool(snapshot_evidence["observer_valid"])
                        and snapshot_observed_at_ms <= plan_created_at_ms
                    )
                    outcome_valid = relationship_valid and (
                        snapshot_eligible == 0
                        or (
                            video_key is not None
                            and video_confirmed_at_ms is not None
                            and plan_created_at_ms
                            <= video_confirmed_at_ms
                            <= completed_value
                        )
                    )
                    unavailable_fallback = (
                        effective_outcome is OutcomeKind.TRACE
                        and str(plan_evidence["quota_reason"] or "")
                        == f"{plan_evidence['requested_outcome']}_unavailable"
                    )
                    terminal_evidence_valid = (
                        unavailable_at_ms is not None
                        and video_confirmed_at_ms is not None
                        and video_confirmed_at_ms
                        <= unavailable_at_ms
                        <= completed_value
                        if unavailable_fallback
                        else effective_outcome is OutcomeKind.TRACE
                        or (
                            confirmed_at_ms is not None
                            and video_confirmed_at_ms is not None
                            and video_confirmed_at_ms
                            <= confirmed_at_ms
                            <= completed_value
                        )
                    )
                    plan_valid = (
                        visit_value <= plan_created_at_ms <= completed_value
                        and started_at_ms < completed_value
                        and snapshot_valid
                        and outcome_valid
                        and terminal_evidence_valid
                    )
                if (
                    visit_value is None
                    or completed_value is None
                    or started_at_ms is None
                    or completed_value <= started_at_ms
                    or visit_value > completed_value
                    or not plan_valid
                ):
                    continue
                timings.append(
                    AssignmentTiming(
                        assignment_id=assignment_id,
                        identity_key=plan_key[0],
                        device_id=plan_key[1],
                        duration_ms=completed_value - started_at_ms,
                    )
                )

            devices_by_target: dict[str, list[str]] = {}
            for timing in timings:
                devices_by_target.setdefault(timing.identity_key, []).append(
                    timing.device_id
                )
            fully_covered_targets = sum(
                len(devices) == expected_devices
                and len(set(devices)) == expected_devices
                for devices in devices_by_target.values()
            )

            uncertain_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM device_action_plans
                    WHERE round_id = ? AND state = 'uncertain'
                    """,
                    (round_id,),
                ).fetchone()["count"]
            )
            deferred_count = sum(
                str(row["phase"]) == AssignmentPhase.DEFERRED.value
                for row in assignment_rows
            )
            identity_mismatch_assignments: set[int] = set()
            history_rows = connection.execute(
                """
                SELECT history.assignment_id, history.details_json,
                       assignment.last_error_code
                FROM assignment_phase_history AS history
                JOIN round_assignments AS assignment
                  ON assignment.assignment_id = history.assignment_id
                WHERE assignment.round_id = ?
                """,
                (round_id,),
            ).fetchall()
            for row in history_rows:
                details = dict(json.loads(str(row["details_json"])))
                codes = (
                    str(details.get("error_code") or ""),
                    str(details.get("reason") or ""),
                    str(row["last_error_code"] or ""),
                )
                if any(
                    "mismatch" in code.lower()
                    and ("identity" in code.lower() or "profile" in code.lower())
                    for code in codes
                ):
                    identity_mismatch_assignments.add(int(row["assignment_id"]))

            missing_quota_plan_ids = {
                int(row["plan_id"])
                for row in connection.execute(
                    """
                    SELECT plan.plan_id
                    FROM device_action_plans AS plan
                    WHERE plan.round_id = ?
                      AND plan.effective_outcome <> 'trace'
                      AND (
                          plan.quota_window_start_ms IS NULL
                          OR plan.quota_window_start_ms <>
                              plan.created_at_ms - plan.created_at_ms % 3600000
                          OR NOT EXISTS (
                              SELECT 1 FROM acquisition_quota_windows AS quota
                              WHERE quota.device_id = plan.device_id
                                AND quota.outcome = plan.effective_outcome
                                AND quota.window_start_ms = plan.quota_window_start_ms
                          )
                      )
                    """,
                    (round_id,),
                )
            }
            quota_overrun_count = len(missing_quota_plan_ids | invalid_outcome_plan_ids)
            quota_limits = {
                outcome.value: limit
                for outcome, limit in _CAPACITY_QUOTA_LIMITS.items()
            }
            quota_rows = connection.execute(
                """
                SELECT DISTINCT quota.device_id, quota.outcome,
                                quota.window_start_ms, quota.reserved_count,
                                quota.confirmed_count, quota.uncertain_count,
                                (
                                    SELECT COUNT(*) FROM device_action_plans AS linked
                                    WHERE linked.device_id = quota.device_id
                                      AND linked.effective_outcome = quota.outcome
                                      AND linked.quota_window_start_ms = quota.window_start_ms
                                ) AS plan_count,
                                (
                                    SELECT COUNT(*) FROM device_action_plans AS linked
                                    WHERE linked.device_id = quota.device_id
                                      AND linked.effective_outcome = quota.outcome
                                      AND linked.quota_window_start_ms = quota.window_start_ms
                                      AND linked.state = 'confirmed'
                                ) AS confirmed_plan_count,
                                (
                                    SELECT COUNT(*) FROM device_action_plans AS linked
                                    WHERE linked.device_id = quota.device_id
                                      AND linked.effective_outcome = quota.outcome
                                      AND linked.quota_window_start_ms = quota.window_start_ms
                                      AND linked.state = 'uncertain'
                                ) AS uncertain_plan_count
                FROM acquisition_quota_windows AS quota
                JOIN device_action_plans AS plan
                  ON plan.device_id = quota.device_id
                 AND plan.quota_window_start_ms = quota.window_start_ms
                 AND (
                      plan.effective_outcome = quota.outcome
                      OR (
                          plan.effective_outcome = 'trace'
                          AND plan.requested_outcome = quota.outcome
                      )
                 )
                WHERE plan.round_id = ?
                """,
                (round_id,),
            ).fetchall()
            for row in quota_rows:
                limit = quota_limits.get(str(row["outcome"]))
                reserved = int(row["reserved_count"])
                confirmed = int(row["confirmed_count"])
                uncertain = int(row["uncertain_count"])
                plan_count = int(row["plan_count"])
                confirmed_plan_count = int(row["confirmed_plan_count"])
                uncertain_plan_count = int(row["uncertain_plan_count"])
                if limit is not None and (
                    reserved > limit
                    or confirmed + uncertain > limit
                    or confirmed + uncertain > reserved
                    or reserved != plan_count
                    or confirmed != confirmed_plan_count
                    or uncertain != uncertain_plan_count
                ):
                    quota_overrun_count += 1

            return RoundCapacityAudit(
                device_ids=device_ids,
                timings=tuple(timings),
                total_assignment_count=len(assignment_rows),
                fully_covered_targets=fully_covered_targets,
                uncertain_count=uncertain_count,
                identity_mismatch_count=len(identity_mismatch_assignments),
                false_success_count=completed_count - len(timings),
                quota_overrun_count=quota_overrun_count,
                deferred_count=deferred_count,
            )

    @classmethod
    def _capacity_snapshot_is_valid(cls, snapshot: Mapping[str, object]) -> bool:
        try:
            access_state = ProfileAccessState(str(snapshot["access_state"]))
            private_value = int(snapshot["private_account"])
            eligible_value = int(snapshot["eligible"])
            if private_value not in {0, 1} or eligible_value not in {0, 1}:
                return False
            counts = (
                snapshot["following_count"],
                snapshot["followers_count"],
                snapshot["post_count"],
            )
            if not (
                all(value is None for value in counts)
                or all(value is not None for value in counts)
            ):
                return False
            metrics = (
                None
                if all(value is None for value in counts)
                else ProfileMetrics(*(int(value) for value in counts))
            )
        except (KeyError, TypeError, ValueError):
            return False

        private_account = bool(private_value)
        if private_account != (access_state is ProfileAccessState.PRIVATE):
            return False
        if access_state in {ProfileAccessState.PUBLIC, ProfileAccessState.PRIVATE}:
            observed_username = cls._normalize_username(
                str(snapshot["observed_username"])
            )
            expected_username = cls._normalize_username(
                str(snapshot["expected_username"])
            )
            if not observed_username or (
                observed_username != expected_username
                and not cls._stable_identity_key(str(snapshot["identity_key"]))
            ):
                return False

        reason = str(snapshot["reason"])
        if access_state is ProfileAccessState.PUBLIC:
            if metrics is None:
                return False
            decision = evaluate_profile(metrics)
            expected_reason = (
                "eligible" if decision.eligible else ",".join(decision.reasons)
            )
            return (
                eligible_value == int(decision.eligible) and reason == expected_reason
            )

        expected_reason = (
            "private_account"
            if access_state is ProfileAccessState.PRIVATE
            else f"profile_{access_state.value}"
        )
        return eligible_value == 0 and reason == expected_reason

    @staticmethod
    def _capacity_plan_relationship_is_valid(
        plan: Mapping[str, object], *, snapshot_eligible: bool
    ) -> tuple[bool, OutcomeKind | None]:
        try:
            requested = OutcomeKind(str(plan["requested_outcome"]))
            effective = OutcomeKind(str(plan["effective_outcome"]))
            created_at_ms = int(plan["created_at_ms"])
            window_start_ms = (
                None
                if plan["quota_window_start_ms"] is None
                else int(plan["quota_window_start_ms"])
            )
        except (KeyError, TypeError, ValueError):
            return False, None
        reason = None if plan["quota_reason"] is None else str(plan["quota_reason"])

        if not snapshot_eligible:
            valid = (
                requested is OutcomeKind.TRACE
                and effective is OutcomeKind.TRACE
                and window_start_ms is None
                and reason == "profile_ineligible"
            )
            return valid, effective

        if requested is OutcomeKind.TRACE:
            valid = (
                effective is OutcomeKind.TRACE
                and window_start_ms is None
                and reason in {None, "pacing_not_due"}
            )
            return valid, effective

        limit = _CAPACITY_QUOTA_LIMITS.get(requested)
        if (
            requested is OutcomeKind.REPOST
            and effective is OutcomeKind.TRACE
            and window_start_ms is None
            and reason == "repost_unavailable"
        ):
            return True, effective
        expected_window_start_ms = created_at_ms - created_at_ms % 3_600_000
        if limit is None or window_start_ms != expected_window_start_ms:
            return False, effective
        if effective is requested:
            return (
                reason is None and bool(plan["effective_quota_matched"]),
                effective,
            )
        if effective is not OutcomeKind.TRACE:
            return False, effective

        try:
            reserved = int(plan["requested_quota_reserved_count"])
        except (KeyError, TypeError, ValueError):
            return False, effective
        valid_fallback = (
            reason == f"{requested.value}_limit_reached" and reserved >= limit
        )
        return valid_fallback, effective

    def claim_snapshot_lease(
        self,
        round_id: str,
        identity_key: str,
        device_id: str,
        *,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        if ttl_ms <= 0:
            raise ValueError("snapshot lease TTL must be positive")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = connection.execute(
                """
                SELECT 1 FROM profile_snapshots
                WHERE round_id = ? AND identity_key = ?
                """,
                (round_id, identity_key),
            ).fetchone()
            if snapshot is not None:
                return False
            assignment = connection.execute(
                """
                SELECT 1 FROM round_assignments
                WHERE round_id = ? AND identity_key = ? AND device_id = ?
                  AND visit_confirmed_at_ms IS NOT NULL
                """,
                (round_id, identity_key, device_id),
            ).fetchone()
            if assignment is None:
                raise ValueError(
                    "device has no confirmed profile visit for snapshot target"
                )
            existing = connection.execute(
                """
                SELECT owner_device_id, expires_at_ms
                FROM profile_snapshot_leases
                WHERE round_id = ? AND identity_key = ?
                """,
                (round_id, identity_key),
            ).fetchone()
            expires_at_ms = now_ms + ttl_ms
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO profile_snapshot_leases(
                        round_id, identity_key, owner_device_id, expires_at_ms
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (round_id, identity_key, device_id, expires_at_ms),
                )
                return True
            if (
                str(existing["owner_device_id"]) == device_id
                or int(existing["expires_at_ms"]) <= now_ms
            ):
                connection.execute(
                    """
                    UPDATE profile_snapshot_leases
                    SET owner_device_id = ?, expires_at_ms = ?
                    WHERE round_id = ? AND identity_key = ?
                    """,
                    (device_id, expires_at_ms, round_id, identity_key),
                )
                return True
            return False

    def publish_profile_snapshot(
        self,
        round_id: str,
        identity_key: str,
        *,
        device_id: str,
        observed_username: str,
        metrics: ProfileMetrics | None,
        private_account: bool,
        observed_at_ms: int,
        access_state: ProfileAccessState | str = ProfileAccessState.PUBLIC,
    ) -> ProfileSnapshot:
        state = ProfileAccessState(access_state)
        is_private = bool(private_account or state is ProfileAccessState.PRIVATE)
        if is_private:
            state = ProfileAccessState.PRIVATE
        if state is ProfileAccessState.PUBLIC and metrics is None:
            raise ValueError("public profile metrics are incomplete")

        normalized_observed_username = self._normalize_username(observed_username)
        if state is ProfileAccessState.PUBLIC:
            decision = evaluate_profile(metrics)
            eligible = decision.eligible
            reason = "eligible" if decision.eligible else ",".join(decision.reasons)
        else:
            eligible = False
            reason = (
                "private_account"
                if state is ProfileAccessState.PRIVATE
                else f"profile_{state.value}"
            )
        candidate = ProfileSnapshot(
            round_id=round_id,
            identity_key=identity_key,
            observed_by_device_id=device_id,
            observed_username=normalized_observed_username,
            metrics=metrics,
            private_account=is_private,
            access_state=state,
            eligible=eligible,
            reason=reason,
            observed_at_ms=observed_at_ms,
        )

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._profile_snapshot(connection, round_id, identity_key)
            if existing is not None:
                if existing == candidate:
                    return existing
                raise ValueError("profile snapshot is immutable")
            target = connection.execute(
                """
                SELECT target.username
                FROM exposure_rounds AS round
                JOIN pool_targets AS target ON target.pool_id = round.pool_id
                WHERE round.round_id = ? AND target.identity_key = ?
                """,
                (round_id, identity_key),
            ).fetchone()
            if target is None:
                raise ValueError("snapshot target is not assigned to the round")
            expected_username = self._normalize_username(str(target["username"]))
            if state in {ProfileAccessState.PUBLIC, ProfileAccessState.PRIVATE} and (
                not normalized_observed_username
                or (
                    normalized_observed_username != expected_username
                    and not self._stable_identity_key(identity_key)
                )
            ):
                raise ValueError("profile snapshot identity mismatch")
            lease = connection.execute(
                """
                SELECT owner_device_id, expires_at_ms
                FROM profile_snapshot_leases
                WHERE round_id = ? AND identity_key = ?
                """,
                (round_id, identity_key),
            ).fetchone()
            if (
                lease is None
                or str(lease["owner_device_id"]) != device_id
                or int(lease["expires_at_ms"])
                <= max(observed_at_ms, int(self.clock_ms()))
            ):
                raise ValueError("device does not hold the snapshot lease")
            connection.execute(
                """
                INSERT INTO profile_snapshots(
                    round_id, identity_key, observed_by_device_id,
                    observed_username, following_count, followers_count,
                    post_count, private_account, access_state, eligible,
                    reason, observed_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.round_id,
                    candidate.identity_key,
                    candidate.observed_by_device_id,
                    candidate.observed_username,
                    None if metrics is None else metrics.following,
                    None if metrics is None else metrics.followers,
                    None if metrics is None else metrics.posts,
                    int(candidate.private_account),
                    candidate.access_state.value,
                    int(candidate.eligible),
                    candidate.reason,
                    candidate.observed_at_ms,
                ),
            )
            connection.execute(
                """
                DELETE FROM profile_snapshot_leases
                WHERE round_id = ? AND identity_key = ?
                """,
                (round_id, identity_key),
            )
            return candidate

    def profile_snapshot(
        self, round_id: str, identity_key: str
    ) -> ProfileSnapshot | None:
        with self._connect() as connection:
            return self._profile_snapshot(connection, round_id, identity_key)

    def create_action_plan(
        self,
        *,
        round_id: str,
        identity_key: str,
        device_id: str,
        seed: str,
        requested_outcome: OutcomeKind | str,
        now_ms: int,
        hourly_limits: Mapping[OutcomeKind, int],
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        requested = OutcomeKind(requested_outcome)
        if now_ms < 0:
            raise ValueError("action plan timestamp must be nonnegative")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._action_plan(connection, round_id, identity_key, device_id)
            if existing is not None:
                return existing
            self._assert_assignment_worker_fence(
                connection,
                round_id=round_id,
                identity_key=identity_key,
                device_id=device_id,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            snapshot = self._profile_snapshot(connection, round_id, identity_key)
            if snapshot is None:
                raise ValueError("profile snapshot is not ready")
            assignment = connection.execute(
                """
                SELECT visit_confirmed_at_ms
                FROM round_assignments
                WHERE round_id = ? AND identity_key = ? AND device_id = ?
                """,
                (round_id, identity_key, device_id),
            ).fetchone()
            if assignment is None or assignment["visit_confirmed_at_ms"] is None:
                raise ValueError(
                    "device has no confirmed profile visit for action plan"
                )

            effective = requested
            quota_window_start_ms: int | None = None
            quota_reason: str | None = None
            if not snapshot.eligible:
                requested = OutcomeKind.TRACE
                effective = OutcomeKind.TRACE
                quota_reason = "profile_ineligible"
            elif requested is not OutcomeKind.TRACE:
                try:
                    limit = int(hourly_limits[requested])
                except KeyError as error:
                    raise ValueError(
                        f"missing hourly limit for {requested.value}"
                    ) from error
                if limit < 0:
                    raise ValueError("hourly limits must be nonnegative")
                quota_window_start_ms = now_ms - now_ms % 3_600_000
                connection.execute(
                    """
                    INSERT OR IGNORE INTO acquisition_quota_windows(
                        device_id, outcome, window_start_ms
                    ) VALUES (?, ?, ?)
                    """,
                    (device_id, requested.value, quota_window_start_ms),
                )
                reserved = connection.execute(
                    """
                    UPDATE acquisition_quota_windows
                    SET reserved_count = reserved_count + 1
                    WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                      AND reserved_count < ?
                    """,
                    (device_id, requested.value, quota_window_start_ms, limit),
                )
                if reserved.rowcount != 1:
                    effective = OutcomeKind.TRACE
                    quota_reason = f"{requested.value}_limit_reached"

            cursor = connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome,
                    quota_window_start_ms, quota_reason, state, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    round_id,
                    identity_key,
                    device_id,
                    seed,
                    requested.value,
                    effective.value,
                    quota_window_start_ms,
                    quota_reason,
                    now_ms,
                ),
            )
            return self._action_plan_by_id(connection, int(cursor.lastrowid))

    def create_paced_action_plan(
        self,
        *,
        round_id: str,
        identity_key: str,
        device_id: str,
        seed: str,
        now_ms: int,
        hourly_limits: Mapping[OutcomeKind, int],
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        if now_ms < 0:
            raise ValueError("action plan timestamp must be nonnegative")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._action_plan(connection, round_id, identity_key, device_id)
            if existing is not None:
                return existing
            self._assert_assignment_worker_fence(
                connection,
                round_id=round_id,
                identity_key=identity_key,
                device_id=device_id,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            snapshot = self._profile_snapshot(connection, round_id, identity_key)
            if snapshot is None:
                raise ValueError("profile snapshot is not ready")
            assignment = connection.execute(
                """
                SELECT visit_confirmed_at_ms FROM round_assignments
                WHERE round_id = ? AND identity_key = ? AND device_id = ?
                """,
                (round_id, identity_key, device_id),
            ).fetchone()
            if assignment is None or assignment["visit_confirmed_at_ms"] is None:
                raise ValueError(
                    "device has no confirmed profile visit for action plan"
                )

            requested = OutcomeKind.TRACE
            effective = OutcomeKind.TRACE
            quota_reason = (
                "profile_ineligible" if not snapshot.eligible else "pacing_not_due"
            )
            quota_window_start_ms: int | None = None
            selected_state: ActionPacingState | None = None
            if snapshot.eligible:
                candidates: list[ActionPacingState] = []
                for outcome in (
                    OutcomeKind.LIKE,
                    OutcomeKind.FAVORITE,
                    OutcomeKind.REPOST,
                ):
                    limit = int(hourly_limits[outcome])
                    if limit <= 0:
                        raise ValueError("hourly limits must be positive")
                    state = self._action_pacing_state(
                        connection,
                        device_id,
                        outcome,
                        now_ms=now_ms,
                        limit=limit,
                    )
                    if state.ready:
                        candidates.append(state)
                if candidates:
                    total_weight = sum(state.limit for state in candidates)
                    selected_weight = int(seed[:16], 16) % total_weight
                    for state in candidates:
                        if selected_weight < state.limit:
                            selected_state = state
                            break
                        selected_weight -= state.limit
                    if selected_state is None:
                        raise RuntimeError("paced outcome selection failed")
                    requested = selected_state.outcome
                    effective = requested
                    quota_reason = None
                    quota_window_start_ms = now_ms - now_ms % 3_600_000
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO acquisition_quota_windows(
                            device_id, outcome, window_start_ms
                        ) VALUES (?, ?, ?)
                        """,
                        (device_id, requested.value, quota_window_start_ms),
                    )
                    reserved = connection.execute(
                        """
                        UPDATE acquisition_quota_windows
                        SET reserved_count = reserved_count + 1
                        WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                          AND reserved_count < ?
                        """,
                        (
                            device_id,
                            requested.value,
                            quota_window_start_ms,
                            selected_state.limit,
                        ),
                    )
                    if reserved.rowcount != 1:
                        requested = OutcomeKind.TRACE
                        effective = OutcomeKind.TRACE
                        quota_reason = f"{selected_state.outcome.value}_limit_reached"
                        quota_window_start_ms = None
                        selected_state = None

            if selected_state is not None:
                connection.execute(
                    """
                    UPDATE action_pacing_state SET tokens = tokens - 1
                    WHERE device_id = ? AND outcome = ? AND tokens >= 1
                    """,
                    (device_id, selected_state.outcome.value),
                )
            cursor = connection.execute(
                """
                INSERT INTO device_action_plans(
                    round_id, identity_key, device_id, seed,
                    requested_outcome, effective_outcome,
                    quota_window_start_ms, quota_reason, state, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    round_id,
                    identity_key,
                    device_id,
                    seed,
                    requested.value,
                    effective.value,
                    quota_window_start_ms,
                    quota_reason,
                    now_ms,
                ),
            )
            return self._action_plan_by_id(connection, int(cursor.lastrowid))

    def action_plan(
        self, round_id: str, identity_key: str, device_id: str
    ) -> ActionPlan | None:
        with self._connect() as connection:
            return self._action_plan(connection, round_id, identity_key, device_id)

    def action_pacing_state(
        self,
        device_id: str,
        outcome: OutcomeKind | str,
        *,
        now_ms: int,
        limit: int,
    ) -> ActionPacingState:
        normalized_device = str(device_id).strip()
        normalized_outcome = OutcomeKind(outcome)
        if (
            not normalized_device
            or normalized_outcome is OutcomeKind.TRACE
            or now_ms < 0
            or limit <= 0
        ):
            raise ValueError("action pacing inputs are invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._action_pacing_state(
                connection,
                normalized_device,
                normalized_outcome,
                now_ms=now_ms,
                limit=limit,
            )

    def consume_action_token(
        self,
        device_id: str,
        outcome: OutcomeKind | str,
        *,
        now_ms: int,
        limit: int,
    ) -> bool:
        normalized_device = str(device_id).strip()
        normalized_outcome = OutcomeKind(outcome)
        if (
            not normalized_device
            or normalized_outcome is OutcomeKind.TRACE
            or now_ms < 0
            or limit <= 0
        ):
            raise ValueError("action pacing inputs are invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._action_pacing_state(
                connection,
                normalized_device,
                normalized_outcome,
                now_ms=now_ms,
                limit=limit,
            )
            if not state.ready:
                return False
            connection.execute(
                """
                UPDATE action_pacing_state SET tokens = tokens - 1
                WHERE device_id = ? AND outcome = ?
                """,
                (normalized_device, normalized_outcome.value),
            )
            return True

    def rolling_action_usage(
        self,
        device_id: str,
        outcome: OutcomeKind | str,
        *,
        now_ms: int,
    ) -> int:
        normalized_outcome = OutcomeKind(outcome)
        if normalized_outcome is OutcomeKind.TRACE:
            return 0
        with self._connect() as connection:
            return self._rolling_action_usage(
                connection, str(device_id).strip(), normalized_outcome, now_ms
            )

    def _action_pacing_state(
        self,
        connection: sqlite3.Connection,
        device_id: str,
        outcome: OutcomeKind,
        *,
        now_ms: int,
        limit: int,
    ) -> ActionPacingState:
        row = connection.execute(
            """
            SELECT tokens, updated_at_ms FROM action_pacing_state
            WHERE device_id = ? AND outcome = ?
            """,
            (device_id, outcome.value),
        ).fetchone()
        if row is None:
            digest = hashlib.sha256(f"{device_id}\0{outcome.value}".encode()).digest()
            tokens = int.from_bytes(digest[:8], "big") / 2**64
            updated_at_ms = now_ms
            connection.execute(
                """
                INSERT INTO action_pacing_state(device_id, outcome, tokens, updated_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (device_id, outcome.value, tokens, now_ms),
            )
        else:
            updated_at_ms = int(row["updated_at_ms"])
            if now_ms < updated_at_ms:
                raise ValueError("action pacing time cannot move backward")
            tokens = min(
                2.0,
                float(row["tokens"]) + (now_ms - updated_at_ms) * limit / 3_600_000,
            )
            connection.execute(
                """
                UPDATE action_pacing_state SET tokens = ?, updated_at_ms = ?
                WHERE device_id = ? AND outcome = ?
                """,
                (tokens, now_ms, device_id, outcome.value),
            )
        rolling_used = self._rolling_action_usage(
            connection, device_id, outcome, now_ms
        )
        ready = tokens >= 1.0 and rolling_used < limit
        wait_ms = 0 if tokens >= 1.0 else math.ceil((1.0 - tokens) * 3_600_000 / limit)
        return ActionPacingState(
            device_id=device_id,
            outcome=outcome,
            tokens=tokens,
            updated_at_ms=now_ms,
            next_due_at_ms=now_ms + wait_ms,
            rolling_used=rolling_used,
            limit=limit,
            ready=ready,
        )

    @staticmethod
    def _rolling_action_usage(
        connection: sqlite3.Connection,
        device_id: str,
        outcome: OutcomeKind,
        now_ms: int,
    ) -> int:
        if now_ms < 0:
            raise ValueError("rolling quota timestamp must be nonnegative")
        row = connection.execute(
            """
            SELECT COUNT(*) AS count FROM device_action_plans
            WHERE device_id = ? AND effective_outcome = ?
              AND created_at_ms > ? AND created_at_ms <= ?
            """,
            (device_id, outcome.value, now_ms - 3_600_000, now_ms),
        ).fetchone()
        return int(row["count"])

    def quota_window(
        self,
        device_id: str,
        outcome: OutcomeKind | str,
        window_start_ms: int,
    ) -> QuotaWindow | None:
        normalized_outcome = OutcomeKind(outcome)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM acquisition_quota_windows
                WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                """,
                (device_id, normalized_outcome.value, window_start_ms),
            ).fetchone()
            if row is None:
                return None
            return QuotaWindow(
                device_id=str(row["device_id"]),
                outcome=OutcomeKind(str(row["outcome"])),
                window_start_ms=int(row["window_start_ms"]),
                reserved_count=int(row["reserved_count"]),
                confirmed_count=int(row["confirmed_count"]),
                uncertain_count=int(row["uncertain_count"]),
            )

    def set_plan_video(self, plan_id: int, video_key: str) -> ActionPlan:
        normalized = str(video_key).strip()
        if not normalized:
            raise ValueError("video key is empty")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._action_plan_by_id(connection, plan_id)
            if plan.video_key is not None and plan.video_key != normalized:
                raise ValueError("action plan video is immutable")
            if plan.video_key is None:
                connection.execute(
                    "UPDATE device_action_plans SET video_key = ? WHERE plan_id = ?",
                    (normalized, plan_id),
                )
            return self._action_plan_by_id(connection, plan_id)

    def mark_action_executing(
        self,
        plan_id: int,
        *,
        now_ms: int | None = None,
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        if worker_fence_token is not None and now_ms is None:
            raise ValueError("fenced action result timestamp is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._action_plan_by_id(connection, plan_id)
            self._assert_action_worker_fence(
                connection,
                plan,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=0 if now_ms is None else now_ms,
            )
            if plan.state is ActionPlanState.CONFIRMED:
                return plan
            if plan.state is ActionPlanState.UNCERTAIN:
                raise ValueError("uncertain action must be reconciled before execution")
            if plan.state is ActionPlanState.PLANNED:
                connection.execute(
                    "UPDATE device_action_plans SET state = 'executing' WHERE plan_id = ?",
                    (plan_id,),
                )
            return self._action_plan_by_id(connection, plan_id)

    def confirm_trace_plan(
        self,
        plan_id: int,
        *,
        now_ms: int | None = None,
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        if worker_fence_token is not None and now_ms is None:
            raise ValueError("fenced action result timestamp is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._action_plan_by_id(connection, plan_id)
            self._assert_action_worker_fence(
                connection,
                plan,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=0 if now_ms is None else now_ms,
            )
            if plan.effective_outcome is not OutcomeKind.TRACE:
                raise ValueError("interaction plan cannot be confirmed as trace")
            connection.execute(
                "UPDATE device_action_plans SET state = 'confirmed' WHERE plan_id = ?",
                (plan_id,),
            )
            return self._action_plan_by_id(connection, plan_id)

    def confirm_action_unavailable_as_trace(
        self,
        plan_id: int,
        *,
        now_ms: int | None = None,
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        if worker_fence_token is not None and now_ms is None:
            raise ValueError("fenced action result timestamp is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._action_plan_by_id(connection, plan_id)
            self._assert_action_worker_fence(
                connection,
                plan,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=0 if now_ms is None else now_ms,
            )
            if plan.requested_outcome is not OutcomeKind.REPOST:
                raise ValueError(
                    "unavailable trace fallback is only supported for repost"
                )
            if plan.effective_outcome is OutcomeKind.TRACE:
                if (
                    plan.state is not ActionPlanState.CONFIRMED
                    or plan.quota_reason != "repost_unavailable"
                ):
                    raise ValueError("trace fallback is not confirmed")
                return plan
            if plan.effective_outcome is not OutcomeKind.REPOST:
                raise ValueError(
                    "unavailable trace fallback is only supported for repost"
                )
            if plan.state is not ActionPlanState.PLANNED:
                raise ValueError("unavailable action result is not reconciled")
            latest_attempt = connection.execute(
                """
                SELECT result FROM action_attempts
                WHERE plan_id = ? ORDER BY attempt_index DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            if (
                latest_attempt is None
                or str(latest_attempt["result"]) != ActionResult.UNAVAILABLE.value
            ):
                raise ValueError("unavailable action evidence is missing")
            if plan.quota_window_start_ms is not None:
                released = connection.execute(
                    """
                    UPDATE acquisition_quota_windows
                    SET reserved_count = CASE
                        WHEN reserved_count > 0 THEN reserved_count - 1 ELSE 0 END
                    WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                      AND reserved_count > 0
                    """,
                    (
                        plan.device_id,
                        plan.effective_outcome.value,
                        plan.quota_window_start_ms,
                    ),
                )
                if released.rowcount != 1:
                    raise ValueError("unavailable action quota reservation is missing")
                connection.execute(
                    """
                    UPDATE action_pacing_state SET tokens = MIN(tokens + 1, 2.0)
                    WHERE device_id = ? AND outcome = ?
                    """,
                    (plan.device_id, plan.effective_outcome.value),
                )
            connection.execute(
                """
                UPDATE device_action_plans
                SET effective_outcome = 'trace', quota_window_start_ms = NULL,
                    quota_reason = ?, state = 'confirmed'
                WHERE plan_id = ?
                """,
                ("repost_unavailable", plan_id),
            )
            return self._action_plan_by_id(connection, plan_id)

    def record_action_result(
        self,
        plan_id: int,
        result: ActionResult | str,
        *,
        now_ms: int,
        diagnostics: DeviceDiagnostics | None = None,
        worker_owner_id: str | None = None,
        worker_account_id: str | None = None,
        worker_fence_token: int | None = None,
    ) -> ActionPlan:
        normalized_result = ActionResult(result)
        diagnostic_value = diagnostics or DeviceDiagnostics()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan = self._action_plan_by_id(connection, plan_id)
            self._assert_action_worker_fence(
                connection,
                plan,
                worker_owner_id=worker_owner_id,
                worker_account_id=worker_account_id,
                worker_fence_token=worker_fence_token,
                now_ms=now_ms,
            )
            attempt_row = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_index), 0) + 1 AS next_index
                FROM action_attempts WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO action_attempts(
                    plan_id, attempt_index, result, diagnostics_json, attempted_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    plan_id,
                    int(attempt_row["next_index"]),
                    normalized_result.value,
                    json.dumps(
                        {
                            "screenshot_path": diagnostic_value.screenshot_path,
                            "ui_summary": diagnostic_value.ui_summary,
                        },
                        ensure_ascii=False,
                    ),
                    now_ms,
                ),
            )
            if plan.state is ActionPlanState.CONFIRMED:
                return plan

            has_quota = (
                plan.effective_outcome is not OutcomeKind.TRACE
                and plan.quota_window_start_ms is not None
            )
            if normalized_result is ActionResult.CONFIRMED:
                if has_quota:
                    connection.execute(
                        """
                        UPDATE acquisition_quota_windows
                        SET confirmed_count = confirmed_count + 1,
                            uncertain_count = CASE
                                WHEN ? = 'uncertain' AND uncertain_count > 0
                                THEN uncertain_count - 1
                                ELSE uncertain_count
                            END
                        WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                        """,
                        (
                            plan.state.value,
                            plan.device_id,
                            plan.effective_outcome.value,
                            plan.quota_window_start_ms,
                        ),
                    )
                next_state = ActionPlanState.CONFIRMED
            elif normalized_result is ActionResult.UNCERTAIN:
                if has_quota and plan.state is not ActionPlanState.UNCERTAIN:
                    connection.execute(
                        """
                        UPDATE acquisition_quota_windows
                        SET uncertain_count = uncertain_count + 1
                        WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                        """,
                        (
                            plan.device_id,
                            plan.effective_outcome.value,
                            plan.quota_window_start_ms,
                        ),
                    )
                next_state = ActionPlanState.UNCERTAIN
            else:
                if has_quota and plan.state is ActionPlanState.UNCERTAIN:
                    connection.execute(
                        """
                        UPDATE acquisition_quota_windows
                        SET uncertain_count = CASE
                            WHEN uncertain_count > 0 THEN uncertain_count - 1 ELSE 0 END
                        WHERE device_id = ? AND outcome = ? AND window_start_ms = ?
                        """,
                        (
                            plan.device_id,
                            plan.effective_outcome.value,
                            plan.quota_window_start_ms,
                        ),
                    )
                next_state = ActionPlanState.PLANNED
            connection.execute(
                "UPDATE device_action_plans SET state = ? WHERE plan_id = ?",
                (next_state.value, plan_id),
            )
            return self._action_plan_by_id(connection, plan_id)

    def action_plan_by_id(self, plan_id: int) -> ActionPlan:
        with self._connect() as connection:
            return self._action_plan_by_id(connection, plan_id)

    @staticmethod
    def _normalize_target(pool_id: str, target: Target, ordinal: int) -> PoolTarget:
        identity_key = target_identity_key(
            sec_uid=target.sec_uid,
            target_id=target.target_id,
            username=target.username,
        )
        return PoolTarget(
            pool_id=pool_id,
            identity_key=identity_key,
            target_id=target.target_id,
            sec_uid=target.sec_uid,
            username=target.username,
            profile_url=target.profile_url,
            source_video_id=target.source_video_id,
            source_line_numbers=tuple(target.source_line_numbers),
            ordinal=ordinal,
        )

    @staticmethod
    def _pool_targets(
        connection: sqlite3.Connection, pool_id: str
    ) -> tuple[PoolTarget, ...]:
        rows = connection.execute(
            "SELECT * FROM pool_targets WHERE pool_id = ? ORDER BY ordinal",
            (pool_id,),
        ).fetchall()
        return tuple(
            PoolTarget(
                pool_id=str(row["pool_id"]),
                identity_key=str(row["identity_key"]),
                target_id=str(row["target_id"]),
                sec_uid=str(row["sec_uid"]),
                username=str(row["username"]),
                profile_url=str(row["profile_url"]),
                source_video_id=str(row["source_video_id"]),
                source_line_numbers=tuple(
                    int(value) for value in json.loads(row["source_line_numbers_json"])
                ),
                ordinal=int(row["ordinal"]),
            )
            for row in rows
        )

    @staticmethod
    def _content_signature(targets: Sequence[PoolTarget]) -> str:
        content = [
            {
                "identity_key": target.identity_key,
                "target_id": target.target_id,
                "sec_uid": target.sec_uid,
                "username": target.username,
                "profile_url": target.profile_url,
                "source_video_id": target.source_video_id,
                "source_line_numbers": target.source_line_numbers,
                "ordinal": target.ordinal,
            }
            for target in targets
        ]
        payload = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def _assignment_by_id(
        connection: sqlite3.Connection, assignment_id: int
    ) -> RoundAssignment:
        row = connection.execute(
            """
            SELECT assignment.*, round.pool_id,
                   target.target_id, target.sec_uid, target.username,
                   target.profile_url, target.source_video_id
            FROM round_assignments AS assignment
            JOIN exposure_rounds AS round
              ON round.round_id = assignment.round_id
            JOIN pool_targets AS target
              ON target.pool_id = round.pool_id
             AND target.identity_key = assignment.identity_key
            WHERE assignment.assignment_id = ?
            """,
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(assignment_id)
        return RoundAssignment(
            assignment_id=int(row["assignment_id"]),
            round_id=str(row["round_id"]),
            pool_id=str(row["pool_id"]),
            identity_key=str(row["identity_key"]),
            target_id=str(row["target_id"]),
            sec_uid=str(row["sec_uid"]),
            username=str(row["username"]),
            profile_url=str(row["profile_url"]),
            source_video_id=str(row["source_video_id"]),
            device_id=str(row["device_id"]),
            order_key=str(row["order_key"]),
            phase=AssignmentPhase(str(row["phase"])),
            attempt_count=int(row["attempt_count"]),
            next_attempt_at_ms=int(row["next_attempt_at_ms"]),
            visit_confirmed_at_ms=(
                None
                if row["visit_confirmed_at_ms"] is None
                else int(row["visit_confirmed_at_ms"])
            ),
            completed_at_ms=(
                None if row["completed_at_ms"] is None else int(row["completed_at_ms"])
            ),
            last_error_code=(
                None if row["last_error_code"] is None else str(row["last_error_code"])
            ),
            lease_owner=(
                None if row["lease_owner"] is None else str(row["lease_owner"])
            ),
            lease_expires_at_ms=int(row["lease_expires_at_ms"]),
        )

    @staticmethod
    def _profile_snapshot(
        connection: sqlite3.Connection, round_id: str, identity_key: str
    ) -> ProfileSnapshot | None:
        row = connection.execute(
            """
            SELECT * FROM profile_snapshots
            WHERE round_id = ? AND identity_key = ?
            """,
            (round_id, identity_key),
        ).fetchone()
        if row is None:
            return None
        counts = (row["following_count"], row["followers_count"], row["post_count"])
        metrics = (
            None
            if any(value is None for value in counts)
            else ProfileMetrics(*(int(value) for value in counts))
        )
        return ProfileSnapshot(
            round_id=str(row["round_id"]),
            identity_key=str(row["identity_key"]),
            observed_by_device_id=str(row["observed_by_device_id"]),
            observed_username=str(row["observed_username"]),
            metrics=metrics,
            private_account=bool(row["private_account"]),
            access_state=ProfileAccessState(str(row["access_state"])),
            eligible=bool(row["eligible"]),
            reason=str(row["reason"]),
            observed_at_ms=int(row["observed_at_ms"]),
        )

    @staticmethod
    def _normalize_username(value: str) -> str:
        return str(value).strip().removeprefix("@").lower()

    @staticmethod
    def _stable_identity_key(identity_key: str) -> bool:
        return str(identity_key).startswith(("sec:", "uid:"))

    @staticmethod
    def _action_plan(
        connection: sqlite3.Connection,
        round_id: str,
        identity_key: str,
        device_id: str,
    ) -> ActionPlan | None:
        row = connection.execute(
            """
            SELECT * FROM device_action_plans
            WHERE round_id = ? AND identity_key = ? AND device_id = ?
            """,
            (round_id, identity_key, device_id),
        ).fetchone()
        return None if row is None else AcquisitionRepository._row_action_plan(row)

    @staticmethod
    def _action_plan_by_id(connection: sqlite3.Connection, plan_id: int) -> ActionPlan:
        row = connection.execute(
            "SELECT * FROM device_action_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return AcquisitionRepository._row_action_plan(row)

    @staticmethod
    def _row_action_plan(row: sqlite3.Row) -> ActionPlan:
        return ActionPlan(
            plan_id=int(row["plan_id"]),
            round_id=str(row["round_id"]),
            identity_key=str(row["identity_key"]),
            device_id=str(row["device_id"]),
            seed=str(row["seed"]),
            requested_outcome=OutcomeKind(str(row["requested_outcome"])),
            effective_outcome=OutcomeKind(str(row["effective_outcome"])),
            quota_window_start_ms=(
                None
                if row["quota_window_start_ms"] is None
                else int(row["quota_window_start_ms"])
            ),
            quota_reason=(
                None if row["quota_reason"] is None else str(row["quota_reason"])
            ),
            video_key=None if row["video_key"] is None else str(row["video_key"]),
            state=ActionPlanState(str(row["state"])),
            created_at_ms=int(row["created_at_ms"]),
        )

    @staticmethod
    def _mark_round_completed_if_terminal(
        connection: sqlite3.Connection, round_id: str
    ) -> None:
        incomplete = connection.execute(
            """
            SELECT 1 FROM round_assignments
            WHERE round_id = ? AND phase NOT IN ('completed', 'skipped')
            LIMIT 1
            """,
            (round_id,),
        ).fetchone()
        if incomplete is None:
            connection.execute(
                "UPDATE exposure_rounds SET state = 'completed' WHERE round_id = ?",
                (round_id,),
            )

    @staticmethod
    def _insert_phase_history(
        connection: sqlite3.Connection,
        assignment_id: int,
        from_phase: AssignmentPhase,
        to_phase: AssignmentPhase,
        changed_at_ms: int,
        details: Mapping[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO assignment_phase_history(
                assignment_id, from_phase, to_phase, details_json, changed_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                assignment_id,
                from_phase.value,
                to_phase.value,
                json.dumps(details, ensure_ascii=False, separators=(",", ":")),
                changed_at_ms,
            ),
        )

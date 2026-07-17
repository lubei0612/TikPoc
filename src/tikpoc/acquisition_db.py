import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from .acquisition_models import (
    AssignmentPhase,
    PoolImport,
    PoolTarget,
    RoundAssignment,
)
from .importer import Target, target_identity_key


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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
                SELECT assignment.assignment_id
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
            connection.execute(
                """
                UPDATE exposure_rounds SET state = 'running'
                WHERE round_id = ? AND state = 'pending'
                """,
                (round_id,),
            )
            return self._assignment_by_id(connection, assignment_id)

    def record_visit_confirmed(
        self, assignment_id: int, owner_id: str, *, now_ms: int
    ) -> None:
        with self._connect() as connection:
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

    def recover_expired_assignment_leases(self, *, now_ms: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE round_assignments
                SET phase = CASE
                        WHEN visit_confirmed_at_ms IS NULL THEN 'pending'
                        ELSE 'deferred'
                    END,
                    lease_owner = NULL,
                    lease_expires_at_ms = 0,
                    next_attempt_at_ms = CASE
                        WHEN next_attempt_at_ms > ? THEN next_attempt_at_ms
                        ELSE ?
                    END
                WHERE lease_owner IS NOT NULL
                  AND lease_expires_at_ms <= ?
                  AND phase <> 'completed'
                """,
                (now_ms, now_ms, now_ms),
            )
            return int(cursor.rowcount)

    def assignment(self, assignment_id: int) -> RoundAssignment:
        with self._connect() as connection:
            return self._assignment_by_id(connection, assignment_id)

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

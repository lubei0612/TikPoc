import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from .acquisition_models import PoolImport, PoolTarget
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

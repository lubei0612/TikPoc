from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .catalog import CatalogProduct


@dataclass(frozen=True)
class PublishingJob:
    job_id: int
    source_key: str
    account_id: str
    caption: str
    asset_paths: tuple[str, ...]
    state: str
    lease_owner: str
    lease_expires_at_ms: int
    visible_post_url: str
    created_at_ms: int
    updated_at_ms: int


class PublishingRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_products (
                    source_key TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    shop_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_time INTEGER,
                    image_urls_json TEXT NOT NULL,
                    discovered_at_ms INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS publishing_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL REFERENCES catalog_products(source_key),
                    account_id TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    asset_paths_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('prepared', 'approved', 'publishing', 'published', 'uncertain', 'rejected')
                    ),
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at_ms INTEGER NOT NULL DEFAULT 0,
                    visible_post_url TEXT NOT NULL DEFAULT '',
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    approved_at_ms INTEGER,
                    finished_at_ms INTEGER,
                    UNIQUE (source_key, account_id)
                );

                CREATE INDEX IF NOT EXISTS publishing_jobs_claim_idx
                ON publishing_jobs (account_id, state, created_at_ms, job_id);
                """
            )

    def prepare_job(
        self,
        product: CatalogProduct,
        *,
        account_id: str,
        caption: str,
        asset_paths: tuple[Path, ...],
        now_ms: int,
    ) -> PublishingJob:
        normalized_account = account_id.strip()
        normalized_caption = caption.strip()
        paths = tuple(str(Path(path)) for path in asset_paths)
        if not normalized_account or not normalized_caption or not paths:
            raise ValueError("account_id, caption, and asset_paths are required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO catalog_products (
                    source_key, source_id, shop_id, title, description,
                    created_time, image_urls_json, discovered_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO NOTHING
                """,
                (
                    product.source_key,
                    product.source_id,
                    product.shop_id,
                    product.title,
                    product.description,
                    product.created_time,
                    json.dumps(product.image_urls),
                    now_ms,
                ),
            )
            connection.execute(
                """
                INSERT INTO publishing_jobs (
                    source_key, account_id, caption, asset_paths_json,
                    state, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, 'prepared', ?, ?)
                ON CONFLICT(source_key, account_id) DO NOTHING
                """,
                (
                    product.source_key,
                    normalized_account,
                    normalized_caption,
                    json.dumps(paths),
                    now_ms,
                    now_ms,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM publishing_jobs
                WHERE source_key = ? AND account_id = ?
                """,
                (product.source_key, normalized_account),
            ).fetchone()
        return _job(row)

    def get_job(self, job_id: int) -> PublishingJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM publishing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _job(row)

    def approve_job(self, job_id: int, *, now_ms: int) -> PublishingJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE publishing_jobs
                SET state = 'approved', approved_at_ms = ?, updated_at_ms = ?
                WHERE job_id = ? AND state = 'prepared'
                """,
                (now_ms, now_ms, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job must be prepared before approval")
            row = connection.execute(
                "SELECT * FROM publishing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _job(row)

    def claim_job(
        self,
        *,
        account_id: str,
        owner: str,
        now_ms: int,
        lease_ms: int,
    ) -> PublishingJob | None:
        if not owner.strip() or lease_ms <= 0:
            raise ValueError("owner and positive lease_ms are required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            busy = connection.execute(
                """
                SELECT 1 FROM publishing_jobs
                WHERE account_id = ? AND state = 'publishing'
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if busy is not None:
                return None
            row = connection.execute(
                """
                SELECT * FROM publishing_jobs
                WHERE account_id = ? AND state = 'approved'
                ORDER BY created_at_ms, job_id
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE publishing_jobs
                SET state = 'publishing', lease_owner = ?,
                    lease_expires_at_ms = ?, updated_at_ms = ?
                WHERE job_id = ? AND state = 'approved'
                """,
                (owner, now_ms + lease_ms, now_ms, row["job_id"]),
            )
            if cursor.rowcount != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM publishing_jobs WHERE job_id = ?", (row["job_id"],)
            ).fetchone()
        return _job(claimed)

    def finish_job(
        self,
        job_id: int,
        *,
        owner: str,
        result: str,
        visible_post_url: str,
        now_ms: int,
    ) -> PublishingJob:
        if result not in {"published", "uncertain"}:
            raise ValueError("result must be published or uncertain")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE publishing_jobs
                SET state = ?, visible_post_url = ?, finished_at_ms = ?,
                    updated_at_ms = ?, lease_expires_at_ms = 0
                WHERE job_id = ? AND state = 'publishing' AND lease_owner = ?
                """,
                (result, visible_post_url.strip(), now_ms, now_ms, job_id, owner),
            )
            if cursor.rowcount != 1:
                raise ValueError("job must be publishing under the same owner")
            row = connection.execute(
                "SELECT * FROM publishing_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _job(row)


def _job(row: sqlite3.Row | None) -> PublishingJob:
    if row is None:
        raise RuntimeError("publishing job row is missing")
    return PublishingJob(
        job_id=int(row["job_id"]),
        source_key=str(row["source_key"]),
        account_id=str(row["account_id"]),
        caption=str(row["caption"]),
        asset_paths=tuple(json.loads(row["asset_paths_json"])),
        state=str(row["state"]),
        lease_owner=str(row["lease_owner"]),
        lease_expires_at_ms=int(row["lease_expires_at_ms"]),
        visible_post_url=str(row["visible_post_url"]),
        created_at_ms=int(row["created_at_ms"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import TaskState


@dataclass(frozen=True)
class Task:
    id: int
    batch_id: str
    target_id: str
    username: str
    state: TaskState
    attempts: int
    checkpoint: str | None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    checkpoint TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(batch_id, target_id)
                )
                """
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

    def insert_task(self, batch_id: str, target_id: str, username: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(batch_id, target_id, username) VALUES (?, ?, ?)",
                (batch_id, target_id, username),
            )
            return int(cursor.lastrowid)

    def claim_next(self) -> Task | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE state IN ('pending', 'retry_wait')
                ORDER BY id
                LIMIT 1
                """
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
            )

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
        processed = sum(counts.get(state, 0) for state in ("completed", "skipped", "failed"))
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

    def record_runtime_event(self, event_type: str, username: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_events(event_type, username) VALUES (?, ?)",
                (event_type, username),
            )

    def latest_runtime_event(self) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT event_type, username, created_at
                FROM runtime_events ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

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

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .acquisition_errors import AcquisitionConflict, AcquisitionNotFound

_COMMAND_FAILURE_KEY = "failure"
_MAX_LEGACY_COMMAND_JSON_LENGTH = 4_096
_KNOWN_COMMAND_CONFLICTS = {
    "assignment has an active lease",
    "assignment is not retryable",
    "assignment state does not allow command",
    "command id has different content",
    "completed assignment does not allow command",
    "terminal assignment does not allow command",
    "device state does not allow command",
    "fleet contains a stopped round",
    "round state does not allow command",
}


class OperatorControlRepository:
    """SQLite boundary for durable, idempotent operator controls."""

    def __init__(self, path: Path, *, clock_ms: Callable[[], int]) -> None:
        self.path = path
        self.clock_ms = clock_ms

    def migrate(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operator_commands (
                    command_id TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            self._backfill_legacy_control_states(connection)

    @staticmethod
    def _backfill_legacy_control_states(connection: sqlite3.Connection) -> None:
        latest: dict[tuple[str, str], tuple[str, str, int]] = {}
        rows = connection.execute(
            """
            SELECT command_id, command_type, payload_json, result_json,
                   created_at_ms
            FROM operator_commands
            WHERE command_type IN ('start', 'pause', 'stop')
            ORDER BY created_at_ms, rowid
            """
        )
        expected_states = {
            "start": "running",
            "pause": "paused",
            "stop": "stopped",
        }
        for row in rows:
            command_id = row["command_id"]
            command_type = row["command_type"]
            payload_json = row["payload_json"]
            result_json = row["result_json"]
            if (
                not isinstance(command_id, str)
                or not 1 <= len(command_id) <= 100
                or not isinstance(command_type, str)
                or not isinstance(payload_json, str)
                or not isinstance(result_json, str)
                or len(payload_json) > _MAX_LEGACY_COMMAND_JSON_LENGTH
                or len(result_json) > _MAX_LEGACY_COMMAND_JSON_LENGTH
            ):
                continue
            try:
                payload = json.loads(payload_json)
                result = json.loads(result_json)
                created_at_ms = int(row["created_at_ms"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                not isinstance(payload, dict)
                or not isinstance(result, dict)
                or _COMMAND_FAILURE_KEY in result
                or created_at_ms < 0
            ):
                continue
            scope = payload.get("scope")
            raw_scope_id = payload.get("scope_id")
            expected_state = expected_states[command_type]
            if (
                scope not in {"device", "assignment"}
                or not isinstance(raw_scope_id, str)
                or result.get("state") != expected_state
            ):
                continue
            scope_id = raw_scope_id.strip()
            if not 1 <= len(scope_id) <= 200:
                continue
            if scope == "assignment":
                try:
                    assignment_id = int(scope_id)
                except ValueError:
                    continue
                if assignment_id <= 0:
                    continue
                scope_id = str(assignment_id)
            latest[(scope, scope_id)] = (
                expected_state,
                command_id,
                created_at_ms,
            )
        connection.executemany(
            """
            INSERT OR IGNORE INTO operator_control_states(
                scope, scope_id, state, command_id, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                (scope, scope_id, state, command_id, created_at_ms)
                for (scope, scope_id), (
                    state,
                    command_id,
                    created_at_ms,
                ) in latest.items()
            ),
        )

    def apply_command(
        self, command_type: str, command_id: str, scope: str, scope_id: str
    ) -> dict[str, object]:
        payload = {"scope": scope, "scope_id": scope_id}
        return self._idempotent_command(
            command_type,
            command_id,
            payload,
            lambda connection: self._apply_control(
                connection, command_id, command_type, scope, scope_id
            ),
        )

    def retry(self, command_id: str, assignment_id: int) -> dict[str, object]:
        payload = {"assignment_id": assignment_id}

        def apply(connection: sqlite3.Connection) -> dict[str, object]:
            row = connection.execute(
                "SELECT phase, lease_owner FROM round_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is None:
                raise AcquisitionNotFound(str(assignment_id))
            if str(row["phase"]) != "deferred" or row["lease_owner"] is not None:
                raise AcquisitionConflict("assignment is not retryable")
            connection.execute(
                "UPDATE round_assignments SET next_attempt_at_ms = 0 WHERE assignment_id = ?",
                (assignment_id,),
            )
            return {
                "command_id": command_id,
                "assignment_id": assignment_id,
                "phase": "deferred",
                "retry_ready": True,
            }

        return self._idempotent_command("retry", command_id, payload, apply)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _idempotent_command(
        self,
        command_type: str,
        command_id: str,
        payload: dict[str, object],
        apply: Callable[[sqlite3.Connection], dict[str, object]],
    ) -> dict[str, object]:
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        failure: dict[str, str] | None = None
        result: dict[str, object] | None = None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT command_type, payload_json, result_json FROM operator_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["command_type"]) != command_type
                    or str(existing["payload_json"]) != payload_json
                ):
                    raise AcquisitionConflict("command id has different content")
                stored = json.loads(str(existing["result_json"]))
                self._raise_stored_failure(stored)
                return stored
            pending_json = json.dumps(
                {"pending": True}, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                """
                INSERT INTO operator_commands(
                    command_id, command_type, payload_json, result_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (command_id, command_type, payload_json, pending_json, self.clock_ms()),
            )
            try:
                result = apply(connection)
            except AcquisitionNotFound:
                failure = {
                    "kind": "not_found",
                    "message": (
                        "assignment not found"
                        if command_type == "retry"
                        else "command target not found"
                    ),
                }
            except AcquisitionConflict as error:
                message = str(error)
                failure = {
                    "kind": "conflict",
                    "message": (
                        message
                        if message in _KNOWN_COMMAND_CONFLICTS
                        else "command conflicts with current state"
                    ),
                }
            stored_result: dict[str, object] = (
                {_COMMAND_FAILURE_KEY: failure} if failure is not None else result or {}
            )
            connection.execute(
                """
                UPDATE operator_commands SET result_json = ? WHERE command_id = ?
                """,
                (
                    json.dumps(stored_result, sort_keys=True, separators=(",", ":")),
                    command_id,
                ),
            )
        if failure is not None:
            self._raise_stored_failure({_COMMAND_FAILURE_KEY: failure})
        assert result is not None
        return result

    @staticmethod
    def _raise_stored_failure(stored: dict[str, object]) -> None:
        raw_failure = stored.get(_COMMAND_FAILURE_KEY)
        if not isinstance(raw_failure, dict):
            return
        kind = raw_failure.get("kind")
        message = raw_failure.get("message")
        if not isinstance(message, str) or len(message) > 200:
            raise AcquisitionConflict("stored command failure is invalid")
        if kind == "not_found":
            raise AcquisitionNotFound(message)
        if kind == "conflict":
            raise AcquisitionConflict(message)
        raise AcquisitionConflict("stored command failure is invalid")

    def _apply_control(
        self,
        connection: sqlite3.Connection,
        command_id: str,
        command_type: str,
        scope: str,
        scope_id: str,
    ) -> dict[str, object]:
        state = {"start": "running", "pause": "paused", "stop": "stopped"}[command_type]
        if scope == "round":
            row = connection.execute(
                "SELECT state FROM exposure_rounds WHERE round_id = ?", (scope_id,)
            ).fetchone()
            if row is None:
                raise AcquisitionNotFound(scope_id)
            current = str(row["state"])
            if current == "completed" or (current == "stopped" and state != "stopped"):
                raise AcquisitionConflict("round state does not allow command")
            connection.execute(
                "UPDATE exposure_rounds SET state = ? WHERE round_id = ?",
                (state, scope_id),
            )
        elif scope == "fleet":
            if scope_id != "all":
                raise AcquisitionNotFound(scope_id)
            connection.execute(
                """
                UPDATE exposure_rounds SET state = ?
                WHERE state NOT IN ('completed', 'stopped')
                """,
                (state,),
            )
        elif scope in {"device", "assignment"}:
            control_scope_id = scope_id.strip()
            if scope == "device":
                exists = connection.execute(
                    """
                    SELECT 1 FROM round_device_seeds WHERE device_id = ? LIMIT 1
                    """,
                    (control_scope_id,),
                ).fetchone()
                if exists is None:
                    raise AcquisitionNotFound(control_scope_id)
            else:
                try:
                    assignment_id = int(control_scope_id)
                except ValueError as error:
                    raise AcquisitionNotFound(control_scope_id) from error
                assignment = connection.execute(
                    """
                    SELECT assignment.phase, assignment.lease_owner,
                           assignment.lease_expires_at_ms
                    FROM round_assignments AS assignment
                    JOIN round_device_seeds AS seed
                      ON seed.round_id = assignment.round_id
                     AND seed.device_id = assignment.device_id
                    WHERE assignment.assignment_id = ?
                    """,
                    (assignment_id,),
                ).fetchone()
                if assignment is None:
                    raise AcquisitionNotFound(control_scope_id)
                control_scope_id = str(assignment_id)
                if str(assignment["phase"]) in {"completed", "skipped"}:
                    raise AcquisitionConflict(
                        "terminal assignment does not allow command"
                    )
                if (
                    command_type in {"pause", "stop"}
                    and assignment["lease_owner"] is not None
                    and int(assignment["lease_expires_at_ms"]) > self.clock_ms()
                ):
                    raise AcquisitionConflict("assignment has an active lease")
            control = connection.execute(
                """
                SELECT state FROM operator_control_states
                WHERE scope = ? AND scope_id = ?
                """,
                (scope, control_scope_id),
            ).fetchone()
            current = "running" if control is None else str(control["state"])
            if current == "stopped" and state != "stopped":
                raise AcquisitionConflict(f"{scope} state does not allow command")
            connection.execute(
                """
                INSERT INTO operator_control_states(
                    scope, scope_id, state, updated_at_ms, command_id
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope, scope_id) DO UPDATE SET
                    state=excluded.state,
                    updated_at_ms=excluded.updated_at_ms,
                    command_id=excluded.command_id
                """,
                (scope, control_scope_id, state, self.clock_ms(), command_id),
            )
        else:
            raise AcquisitionConflict("control scope is not supported")
        return {
            "command_id": command_id,
            "command": command_type,
            "scope": scope,
            "scope_id": scope_id,
            "state": state,
        }

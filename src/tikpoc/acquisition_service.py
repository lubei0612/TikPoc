import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .acquisition_db import AcquisitionRepository
from .importer import read_targets
from .rounds import create_exposure_round


_QUOTA_LIMITS = {"like": 100, "favorite": 14, "repost": 25}
_COMMAND_FAILURE_KEY = "failure"
_MAX_LEGACY_COMMAND_JSON_LENGTH = 4_096
_MAX_DIAGNOSTIC_SCREENSHOT_BYTES = 10 * 1024 * 1024
_SCREENSHOT_MEDIA_TYPES = {
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _is_supported_image(path: Path, suffix: str) -> bool:
    try:
        content = path.read_bytes()
    except OSError:
        return False
    if suffix == ".png":
        return (
            len(content) >= 45
            and content.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
            and content[-12:-8] == b"\x00\x00\x00\x00"
            and content[-8:-4] == b"IEND"
        )
    if suffix in {".jpeg", ".jpg"}:
        return (
            len(content) >= 4
            and content.startswith(b"\xff\xd8\xff")
            and content.endswith(b"\xff\xd9")
        )
    if suffix == ".webp":
        return (
            len(content) >= 20
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
            and content[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}
            and int.from_bytes(content[4:8], "little") + 8 == len(content)
        )
    return False


_KNOWN_COMMAND_CONFLICTS = {
    "assignment has an active lease",
    "assignment is not retryable",
    "assignment state does not allow command",
    "command id has different content",
    "completed assignment does not allow command",
    "device state does not allow command",
    "fleet contains a stopped round",
    "round state does not allow command",
}


class AcquisitionNotFound(KeyError):
    pass


class AcquisitionConflict(ValueError):
    pass


class AcquisitionService:
    def __init__(
        self,
        repository: AcquisitionRepository,
        *,
        clock_ms: Callable[[], int],
        import_roots: Sequence[Path],
    ) -> None:
        self.repository = repository
        self.path = repository.path
        self.clock_ms = clock_ms
        self.import_roots = tuple(root.resolve() for root in import_roots)

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

    def pools(self, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) FROM target_pools").fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT pool_id, source_name, unique_targets, source_rows, created_at_ms
                FROM target_pools ORDER BY created_at_ms DESC, pool_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def rounds(self, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            total = int(
                connection.execute("SELECT COUNT(*) FROM exposure_rounds").fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT round.round_id, round.pool_id, round.state, round.starts_at_ms,
                       round.created_at_ms, pool.unique_targets AS target_count,
                       COUNT(seed.device_id) AS device_count
                FROM exposure_rounds AS round
                JOIN target_pools AS pool ON pool.pool_id = round.pool_id
                LEFT JOIN round_device_seeds AS seed ON seed.round_id = round.round_id
                GROUP BY round.round_id
                ORDER BY round.created_at_ms DESC, round.round_id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def import_pool(self, local_path: str) -> dict[str, object]:
        source = Path(local_path).expanduser().resolve()
        if not any(source.is_relative_to(root) for root in self.import_roots):
            raise AcquisitionConflict("import path is outside configured roots")
        if not source.is_file() or source.suffix.lower() not in {
            ".csv",
            ".xlsx",
            ".xlsm",
        }:
            raise AcquisitionConflict("import source is not a supported file")
        imported_targets = read_targets(source)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        imported = self.repository.import_pool(
            source.name, checksum, imported_targets.targets
        )
        return {
            "pool_id": imported.pool_id,
            "unique_targets": imported.unique_targets,
            "source_rows": imported.source_rows,
            "skipped_duplicates": imported_targets.skipped_duplicates,
            "skipped_invalid": imported_targets.skipped_invalid,
        }

    def create_round(
        self,
        *,
        pool_id: str,
        device_seeds: Mapping[str, str],
        starts_at_ms: int,
        min_inter_device_gap_ms: int,
        min_repeat_gap_ms: int,
    ) -> dict[str, object]:
        if not self.repository.pool_exists(pool_id):
            raise AcquisitionNotFound(pool_id)
        round_id = create_exposure_round(
            self.repository,
            pool_id=pool_id,
            device_seeds=device_seeds,
            starts_at_ms=starts_at_ms,
            min_inter_device_gap_ms=min_inter_device_gap_ms,
            min_repeat_gap_ms=min_repeat_gap_ms,
        )
        return {
            "round_id": round_id,
            "pool_id": pool_id,
            "device_count": len(device_seeds),
            "assignment_count": self.repository.assignment_count(round_id),
        }

    def operations(self, round_id: str) -> dict[str, object]:
        with self._read_connection() as connection:
            round_row = connection.execute(
                """
                SELECT round.round_id, round.pool_id, round.state,
                       round.starts_at_ms, pool.unique_targets AS target_count
                FROM exposure_rounds AS round
                JOIN target_pools AS pool ON pool.pool_id = round.pool_id
                WHERE round.round_id = ?
                """,
                (round_id,),
            ).fetchone()
            if round_row is None:
                raise AcquisitionNotFound(round_id)
            device_rows = self._device_rows(connection, round_id)
            coverage = self._coverage_summary(connection, round_id)
            quotas = self._quota_rows(connection, round_id)
            recent_traces = self._recent_traces(connection, round_id, limit=100)
            diagnostics = self._latest_diagnostics(connection, round_id)
            browser_health = self._browser_health_rows(connection)
        for device in device_rows:
            device["latest_diagnostic"] = diagnostics.get(str(device["device_id"]))
        return {
            "round": dict(round_row),
            "devices": device_rows,
            "quotas": quotas,
            "coverage": coverage,
            "recent_mobile_traces": recent_traces,
            "browser_health": browser_health,
        }

    def coverage(self, round_id: str, *, offset: int, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            round_row = connection.execute(
                "SELECT pool_id FROM exposure_rounds WHERE round_id = ?", (round_id,)
            ).fetchone()
            if round_row is None:
                raise AcquisitionNotFound(round_id)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pool_targets WHERE pool_id = ?",
                    (str(round_row["pool_id"]),),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT assignment.assignment_id, assignment.identity_key,
                       target.username, assignment.device_id, assignment.phase,
                       assignment.attempt_count, assignment.next_attempt_at_ms,
                       assignment.visit_confirmed_at_ms,
                       assignment.completed_at_ms, assignment.last_error_code,
                       plan.effective_outcome,
                       COALESCE(control.state, 'running') AS control_state
                FROM round_assignments AS assignment
                JOIN exposure_rounds AS round ON round.round_id = assignment.round_id
                JOIN pool_targets AS target
                  ON target.pool_id = round.pool_id
                 AND target.identity_key = assignment.identity_key
                LEFT JOIN device_action_plans AS plan
                  ON plan.round_id = assignment.round_id
                 AND plan.identity_key = assignment.identity_key
                 AND plan.device_id = assignment.device_id
                LEFT JOIN operator_control_states AS control
                  ON control.scope = 'assignment'
                 AND control.scope_id = CAST(assignment.assignment_id AS TEXT)
                WHERE assignment.round_id = ?
                  AND assignment.identity_key IN (
                      SELECT identity_key FROM pool_targets WHERE pool_id = ?
                      ORDER BY ordinal LIMIT ? OFFSET ?
                  )
                ORDER BY target.ordinal, assignment.device_id
                """,
                (round_id, str(round_row["pool_id"]), limit, offset),
            ).fetchall()
        items_by_identity: dict[str, dict[str, object]] = {}
        for row in rows:
            identity_key = str(row["identity_key"])
            item = items_by_identity.setdefault(
                identity_key,
                {
                    "identity_key": identity_key,
                    "username": str(row["username"]),
                    "devices": [],
                },
            )
            completed_at = row["completed_at_ms"]
            visited_at = row["visit_confirmed_at_ms"]
            duration_ms = (
                None
                if completed_at is None or visited_at is None
                else max(0, int(completed_at) - int(visited_at))
            )
            item["devices"].append(
                {
                    "assignment_id": int(row["assignment_id"]),
                    "device_id": str(row["device_id"]),
                    "phase": str(row["phase"]),
                    "planned_outcome": row["effective_outcome"],
                    "visit_confirmed": visited_at is not None,
                    "completed": str(row["phase"]) == "completed",
                    "duration_ms": duration_ms,
                    "attempt_count": int(row["attempt_count"]),
                    "next_attempt_at_ms": int(row["next_attempt_at_ms"]),
                    "last_error_code": row["last_error_code"],
                    "control_state": str(row["control_state"]),
                }
            )
        return {
            "round_id": round_id,
            "items": list(items_by_identity.values()),
            "total": total,
            "offset": offset,
            "limit": limit,
        }

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

    def diagnostics(self, assignment_id: int, *, limit: int) -> dict[str, object]:
        with self._read_connection() as connection:
            assignment = connection.execute(
                """
                SELECT assignment_id, round_id, identity_key, device_id, phase,
                       attempt_count, last_error_code
                FROM round_assignments WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
            if assignment is None:
                raise AcquisitionNotFound(str(assignment_id))
            rows = connection.execute(
                """
                SELECT attempt.attempt_id, attempt.attempt_index, attempt.result,
                       attempt.diagnostics_json, attempt.attempted_at_ms
                FROM action_attempts AS attempt
                JOIN device_action_plans AS plan ON plan.plan_id = attempt.plan_id
                JOIN round_assignments AS assignment
                  ON assignment.round_id = plan.round_id
                 AND assignment.identity_key = plan.identity_key
                 AND assignment.device_id = plan.device_id
                WHERE assignment.assignment_id = ?
                ORDER BY attempt.attempted_at_ms DESC, attempt.attempt_id DESC
                LIMIT ?
                """,
                (assignment_id, limit),
            ).fetchall()
        attempts = []
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                diagnostic = {}
            screenshot_path = str(diagnostic.get("screenshot_path") or "")
            attempts.append(
                {
                    "attempt_id": int(row["attempt_id"]),
                    "attempt_index": int(row["attempt_index"]),
                    "result": str(row["result"]),
                    "attempted_at_ms": int(row["attempted_at_ms"]),
                    "ui_summary": str(diagnostic.get("ui_summary") or "")[:500],
                    "screenshot_id": (
                        hashlib.sha256(screenshot_path.encode()).hexdigest()[:24]
                        if screenshot_path
                        else None
                    ),
                }
            )
        payload = dict(assignment)
        payload["attempts"] = attempts
        return payload

    def diagnostic_screenshot(self, screenshot_id: str) -> tuple[Path, str]:
        if not re.fullmatch(r"[0-9a-f]{24}", screenshot_id):
            raise AcquisitionNotFound(screenshot_id)
        evidence_root = (self.path.parent / "screenshots").resolve()
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT diagnostics_json FROM action_attempts"
            ).fetchall()
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                continue
            raw_path = str(diagnostic.get("screenshot_path") or "")
            if not raw_path:
                continue
            candidate_id = hashlib.sha256(raw_path.encode()).hexdigest()[:24]
            if candidate_id != screenshot_id:
                continue
            candidate = Path(raw_path).expanduser().resolve()
            media_type = _SCREENSHOT_MEDIA_TYPES.get(candidate.suffix.lower())
            try:
                stat = candidate.stat()
            except OSError as error:
                raise AcquisitionNotFound(screenshot_id) from error
            if (
                not candidate.is_relative_to(evidence_root)
                or not candidate.is_file()
                or media_type is None
                or stat.st_size > _MAX_DIAGNOSTIC_SCREENSHOT_BYTES
                or not _is_supported_image(candidate, candidate.suffix.lower())
            ):
                raise AcquisitionNotFound(screenshot_id)
            return candidate, media_type
        raise AcquisitionNotFound(screenshot_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _read_connection(self) -> sqlite3.Connection:
        connection = self._connect()
        connection.execute("BEGIN")
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
                if str(assignment["phase"]) == "completed":
                    raise AcquisitionConflict(
                        "completed assignment does not allow command"
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

    def _device_rows(
        self, connection: sqlite3.Connection, round_id: str
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT seed.device_id, health.account_id, health.state AS health,
                   health.error_code, health.updated_at_ms,
                   COALESCE(device_control.state, 'running') AS control_state,
                   assignment.assignment_id, assignment.identity_key,
                   assignment.phase, assignment.attempt_count,
                   assignment.last_error_code, assignment.visit_confirmed_at_ms,
                   assignment.completed_at_ms,
                   COALESCE(assignment_control.state, 'running')
                       AS assignment_control_state
            FROM round_device_seeds AS seed
            LEFT JOIN fleet_device_health AS health ON health.device_id = seed.device_id
            LEFT JOIN operator_control_states AS device_control
              ON device_control.scope = 'device'
             AND device_control.scope_id = seed.device_id
            LEFT JOIN round_assignments AS assignment
              ON assignment.assignment_id = (
                  SELECT candidate.assignment_id FROM round_assignments AS candidate
                  WHERE candidate.round_id = seed.round_id
                    AND candidate.device_id = seed.device_id
                    AND candidate.phase IN (
                        'profile_opening', 'identity_confirmed', 'waiting_snapshot',
                        'video_opening', 'video_confirmed', 'quota_reserved',
                        'action_executing', 'action_reconciling'
                    )
                  ORDER BY candidate.assignment_id LIMIT 1
              )
            LEFT JOIN operator_control_states AS assignment_control
              ON assignment_control.scope = 'assignment'
             AND assignment_control.scope_id = CAST(assignment.assignment_id AS TEXT)
            WHERE seed.round_id = ? ORDER BY seed.device_id
            """,
            (round_id,),
        ).fetchall()
        duration_rows = connection.execute(
            """
            WITH first_claims AS (
                SELECT history.assignment_id,
                       MIN(history.changed_at_ms) AS started_at_ms
                FROM assignment_phase_history AS history
                JOIN round_assignments AS assignment
                  ON assignment.assignment_id = history.assignment_id
                WHERE assignment.round_id = ?
                  AND history.to_phase = 'profile_opening'
                GROUP BY history.assignment_id
            )
            SELECT assignment.device_id,
                   assignment.completed_at_ms - first_claims.started_at_ms
                       AS duration_ms
            FROM round_assignments AS assignment
            JOIN first_claims ON first_claims.assignment_id = assignment.assignment_id
            WHERE assignment.round_id = ? AND assignment.phase = 'completed'
              AND assignment.completed_at_ms > first_claims.started_at_ms
            ORDER BY assignment.device_id, duration_ms
            """,
            (round_id, round_id),
        ).fetchall()
        durations: dict[str, list[int]] = {}
        for row in duration_rows:
            durations.setdefault(str(row["device_id"]), []).append(
                int(row["duration_ms"])
            )
        devices = []
        for row in rows:
            values = durations.get(str(row["device_id"]), [])
            mean_ms = 0.0 if not values else sum(values) / len(values)
            p90_ms = (
                0.0
                if not values
                else float(values[max(0, math.ceil(len(values) * 0.9) - 1)])
            )
            assignment = None
            if row["assignment_id"] is not None:
                assignment = {
                    "assignment_id": int(row["assignment_id"]),
                    "identity_key": str(row["identity_key"]),
                    "phase": str(row["phase"]),
                    "attempt_count": int(row["attempt_count"]),
                    "last_error_code": row["last_error_code"],
                    "control_state": str(row["assignment_control_state"]),
                }
            devices.append(
                {
                    "device_id": str(row["device_id"]),
                    "account_id": row["account_id"],
                    "health": str(row["health"] or "unknown"),
                    "health_error_code": row["error_code"],
                    "health_updated_at_ms": row["updated_at_ms"],
                    "control_state": str(row["control_state"]),
                    "current_assignment": assignment,
                    "mean_ms": mean_ms,
                    "p90_ms": p90_ms,
                }
            )
        return devices

    @staticmethod
    def _browser_health_rows(
        connection: sqlite3.Connection,
    ) -> list[dict[str, object]]:
        rows = connection.execute(
            """
            SELECT account_id, page_role, device_id, status,
                   observed_at_ms, detail
            FROM browser_account_health
            ORDER BY account_id, page_role
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def _latest_diagnostics(
        self, connection: sqlite3.Connection, round_id: str
    ) -> dict[str, dict[str, object]]:
        rows = connection.execute(
            """
            WITH ranked AS (
                SELECT plan.device_id, attempt.result, attempt.diagnostics_json,
                       attempt.attempted_at_ms,
                       ROW_NUMBER() OVER (
                           PARTITION BY plan.device_id
                           ORDER BY attempt.attempted_at_ms DESC, attempt.attempt_id DESC
                       ) AS rank
                FROM action_attempts AS attempt
                JOIN device_action_plans AS plan ON plan.plan_id = attempt.plan_id
                WHERE plan.round_id = ?
            )
            SELECT device_id, result, diagnostics_json, attempted_at_ms
            FROM ranked WHERE rank = 1
            """,
            (round_id,),
        ).fetchall()
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            try:
                diagnostic = json.loads(str(row["diagnostics_json"]))
            except (TypeError, json.JSONDecodeError):
                diagnostic = {}
            screenshot_path = str(diagnostic.get("screenshot_path") or "")
            result[str(row["device_id"])] = {
                "result": str(row["result"]),
                "attempted_at_ms": int(row["attempted_at_ms"]),
                "ui_summary": str(diagnostic.get("ui_summary") or "")[:500],
                "screenshot_id": (
                    hashlib.sha256(screenshot_path.encode()).hexdigest()[:24]
                    if screenshot_path
                    else None
                ),
            }
        return result

    def _coverage_summary(
        self, connection: sqlite3.Connection, round_id: str
    ) -> dict[str, object]:
        required_devices = int(
            connection.execute(
                "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT identity_key,
                   SUM(visit_confirmed_at_ms IS NOT NULL) AS confirmed_devices,
                   SUM(phase = 'completed') AS completed_devices
            FROM round_assignments WHERE round_id = ? GROUP BY identity_key
            """,
            (round_id,),
        ).fetchall()
        targets = len(rows)
        confirmed_visits = sum(int(row["confirmed_devices"]) for row in rows)
        completed_assignments = sum(int(row["completed_devices"]) for row in rows)
        fully_covered = sum(
            int(row["confirmed_devices"]) == required_devices for row in rows
        )
        fully_completed = sum(
            int(row["completed_devices"]) == required_devices for row in rows
        )
        return {
            "targets": targets,
            "required_devices": required_devices,
            "confirmed_visits": confirmed_visits,
            "completed_assignments": completed_assignments,
            "fully_covered": fully_covered,
            "fully_completed": fully_completed,
            "coverage_rate": 0.0 if targets == 0 else fully_covered / targets,
            "completion_rate": 0.0 if targets == 0 else fully_completed / targets,
        }

    def _quota_rows(
        self, connection: sqlite3.Connection, round_id: str
    ) -> list[dict[str, object]]:
        now_ms = self.clock_ms()
        window_start_ms = now_ms - 3_600_000
        windows = {
            (str(row["device_id"]), str(row["outcome"])): row
            for row in connection.execute(
                """
                SELECT device_id, effective_outcome AS outcome,
                       COUNT(*) AS reserved_count,
                       SUM(state = 'confirmed') AS confirmed_count,
                       SUM(state = 'uncertain') AS uncertain_count
                FROM device_action_plans
                WHERE effective_outcome <> 'trace'
                  AND created_at_ms > ? AND created_at_ms <= ?
                GROUP BY device_id, effective_outcome
                """,
                (window_start_ms, now_ms),
            )
        }
        pacing = {
            (str(row["device_id"]), str(row["outcome"])): row
            for row in connection.execute(
                """
                SELECT device_id, outcome, tokens, updated_at_ms
                FROM action_pacing_state
                """
            )
        }
        device_ids = [
            str(row["device_id"])
            for row in connection.execute(
                "SELECT device_id FROM round_device_seeds WHERE round_id = ? ORDER BY device_id",
                (round_id,),
            )
        ]
        results = []
        for device_id in device_ids:
            for outcome, limit in _QUOTA_LIMITS.items():
                row = windows.get((device_id, outcome))
                reserved = 0 if row is None else int(row["reserved_count"])
                confirmed = 0 if row is None else int(row["confirmed_count"])
                uncertain = 0 if row is None else int(row["uncertain_count"])
                pacing_row = pacing.get((device_id, outcome))
                if pacing_row is None:
                    digest = hashlib.sha256(f"{device_id}\0{outcome}".encode()).digest()
                    tokens = int.from_bytes(digest[:8], "big") / 2**64
                else:
                    elapsed_ms = max(0, now_ms - int(pacing_row["updated_at_ms"]))
                    tokens = min(
                        2.0,
                        float(pacing_row["tokens"]) + elapsed_ms * limit / 3_600_000,
                    )
                token_ready = tokens >= 1 and reserved < limit
                wait_ms = (
                    0 if tokens >= 1 else math.ceil((1 - tokens) * 3_600_000 / limit)
                )
                results.append(
                    {
                        "device_id": device_id,
                        "outcome": outcome,
                        "limit": limit,
                        "reserved": reserved,
                        "confirmed": confirmed,
                        "uncertain": uncertain,
                        "remaining": max(0, limit - reserved),
                        "rolling_window_started_at_ms": window_start_ms,
                        "token_ready": token_ready,
                        "next_due_at_ms": now_ms + wait_ms,
                        "candidate_weight": limit if token_ready else 0,
                    }
                )
        return results

    def _recent_traces(
        self, connection: sqlite3.Connection, round_id: str, *, limit: int
    ) -> list[dict[str, object]]:
        required_devices = int(
            connection.execute(
                "SELECT COUNT(*) FROM round_device_seeds WHERE round_id = ?",
                (round_id,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT assignment.identity_key, target.username,
                   SUM(assignment.visit_confirmed_at_ms IS NOT NULL) AS confirmed_devices,
                   SUM(assignment.phase = 'completed') AS completed_devices,
                   MAX(assignment.visit_confirmed_at_ms) AS last_visit_confirmed_at_ms
            FROM round_assignments AS assignment
            JOIN exposure_rounds AS round ON round.round_id = assignment.round_id
            JOIN pool_targets AS target
              ON target.pool_id = round.pool_id
             AND target.identity_key = assignment.identity_key
            WHERE assignment.round_id = ?
            GROUP BY assignment.identity_key, target.username
            HAVING confirmed_devices > 0
            ORDER BY last_visit_confirmed_at_ms DESC, assignment.identity_key
            LIMIT ?
            """,
            (round_id, limit),
        ).fetchall()
        return [
            {
                "identity_key": str(row["identity_key"]),
                "username": str(row["username"]),
                "confirmed_devices": int(row["confirmed_devices"]),
                "completed_devices": int(row["completed_devices"]),
                "required_devices": required_devices,
                "fully_covered": int(row["confirmed_devices"]) == required_devices,
                "fully_completed": int(row["completed_devices"]) == required_devices,
                "last_visit_confirmed_at_ms": int(row["last_visit_confirmed_at_ms"]),
            }
            for row in rows
        ]

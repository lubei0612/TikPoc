import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from .acquisition_db import AcquisitionRepository
from .db import Database
from .importer import read_targets
from .rounds import create_exposure_round


_QUOTA_LIMITS = {"like": 100, "favorite": 14, "repost": 25}


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
        database: Database | None = None,
    ) -> None:
        self.repository = repository
        self.path = repository.path
        self.clock_ms = clock_ms
        self.import_roots = tuple(root.resolve() for root in import_roots)
        self.database = database or Database(repository.path)

    def migrate(self) -> None:
        with self._connect() as connection:
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
        for device in device_rows:
            device["latest_diagnostic"] = diagnostics.get(str(device["device_id"]))
        browser_health = self.database.browser_health_snapshot()
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
                       plan.effective_outcome
                FROM round_assignments AS assignment
                JOIN exposure_rounds AS round ON round.round_id = assignment.round_id
                JOIN pool_targets AS target
                  ON target.pool_id = round.pool_id
                 AND target.identity_key = assignment.identity_key
                LEFT JOIN device_action_plans AS plan
                  ON plan.round_id = assignment.round_id
                 AND plan.identity_key = assignment.identity_key
                 AND plan.device_id = assignment.device_id
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
                return json.loads(str(existing["result_json"]))
            result = apply(connection)
            result_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO operator_commands(
                    command_id, command_type, payload_json, result_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (command_id, command_type, payload_json, result_json, self.clock_ms()),
            )
            return result

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
            rows = connection.execute(
                "SELECT round_id, state FROM exposure_rounds"
            ).fetchall()
            if state != "stopped" and any(
                str(row["state"]) == "stopped" for row in rows
            ):
                raise AcquisitionConflict("fleet contains a stopped round")
            connection.execute(
                """
                UPDATE exposure_rounds SET state = ?
                WHERE state NOT IN ('completed', 'stopped')
                   OR (state = 'stopped' AND ? = 'stopped')
                """,
                (state, state),
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
                   assignment.assignment_id, assignment.identity_key,
                   assignment.phase, assignment.attempt_count,
                   assignment.last_error_code, assignment.visit_confirmed_at_ms,
                   assignment.completed_at_ms
            FROM round_device_seeds AS seed
            LEFT JOIN fleet_device_health AS health ON health.device_id = seed.device_id
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
                }
            devices.append(
                {
                    "device_id": str(row["device_id"]),
                    "account_id": row["account_id"],
                    "health": str(row["health"] or "unknown"),
                    "health_error_code": row["error_code"],
                    "health_updated_at_ms": row["updated_at_ms"],
                    "current_assignment": assignment,
                    "mean_ms": mean_ms,
                    "p90_ms": p90_ms,
                }
            )
        return devices

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
        window_start_ms = now_ms - now_ms % 3_600_000
        windows = {
            (str(row["device_id"]), str(row["outcome"])): row
            for row in connection.execute(
                """
                SELECT device_id, outcome, reserved_count, confirmed_count,
                       uncertain_count
                FROM acquisition_quota_windows WHERE window_start_ms = ?
                """,
                (window_start_ms,),
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
                results.append(
                    {
                        "device_id": device_id,
                        "outcome": outcome,
                        "limit": limit,
                        "reserved": reserved,
                        "confirmed": confirmed,
                        "uncertain": uncertain,
                        "remaining": max(0, limit - reserved),
                        "resets_at_ms": window_start_ms + 3_600_000,
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

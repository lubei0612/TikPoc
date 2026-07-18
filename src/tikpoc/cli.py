import argparse
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .acquisition_db import AcquisitionRepository
from .capacity import evaluate_round_capacity
from .db import Database
from .fleet import FleetConfig
from .importer import read_targets
from .interactions import ActionPolicy, InteractionPolicy
from .rounds import create_exposure_round


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tikpoc")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("csv", type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("csv", type=Path)
    import_command.add_argument("--db", type=Path, required=True)
    import_command.add_argument("--device-id", action="append", default=[])
    pool_import = commands.add_parser("pool-import")
    pool_import.add_argument("--db", type=Path, required=True)
    pool_import.add_argument("--csv", type=Path, required=True)
    round_create = commands.add_parser("round-create")
    round_create.add_argument("--db", type=Path, required=True)
    round_create.add_argument("--pool", required=True)
    round_create.add_argument("--devices", type=Path, required=True)
    round_create.add_argument("--starts-at", required=True)
    fleet_run = commands.add_parser("fleet-run")
    fleet_run.add_argument("--db", type=Path, required=True)
    fleet_run.add_argument("--round", required=True)
    fleet_run.add_argument("--devices", type=Path, required=True)
    assignment_retry = commands.add_parser("assignment-retry")
    assignment_retry.add_argument("--db", type=Path, required=True)
    assignment_retry.add_argument("--assignment", type=int, required=True)
    capacity = commands.add_parser("capacity")
    capacity.add_argument("--db", type=Path, required=True)
    capacity.add_argument("--round", required=True)
    capacity.add_argument("--expected-devices", type=int, default=7)
    capacity.add_argument("--target-count", type=int, default=10_000)
    capacity.add_argument("--effective-hours", type=float, default=20)
    capacity.add_argument("--json", action="store_true", dest="json_output")
    status = commands.add_parser("status")
    status.add_argument("--db", type=Path, required=True)
    for command_name in ("serve", "dashboard"):
        serve = commands.add_parser(command_name)
        serve.add_argument("--db", type=Path, required=True)
        serve.add_argument(
            "--host",
            choices=("127.0.0.1", "::1", "localhost"),
            default="127.0.0.1",
        )
        serve.add_argument("--port", type=int, default=8765)
        serve.add_argument("--web-accounts", type=Path)
        serve.add_argument("--env-file", type=Path, default=Path(".env.local"))
        serve.add_argument("--with-web-worker", action="store_true")
        serve.add_argument("--web-worker-idle-sleep", type=float, default=1.0)
    web_worker = commands.add_parser("web-worker")
    web_worker.add_argument("--db", type=Path, required=True)
    web_worker.add_argument("--web-accounts", type=Path)
    web_worker.add_argument("--env-file", type=Path, default=Path(".env.local"))
    web_worker.add_argument("--idle-sleep", type=float, default=1.0)
    web_worker.add_argument("--once", action="store_true")
    run = commands.add_parser("run")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--appium-url", default="http://127.0.0.1:4723")
    run.add_argument("--udid", default="emulator-5554")
    run.add_argument("--device-id", default="default")
    for action in ("like", "favorite", "share"):
        run.add_argument(f"--{action}-probability", type=float, default=0.0)
        run.add_argument(f"--{action}-hourly-limit", type=int, default=0)
    run.add_argument("--trace-probability", type=float, default=1.0)
    run.add_argument("--event-driven", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "pool-import":
        _require_file(args.csv, "CSV file")
        result = read_targets(args.csv)
        checksum = hashlib.sha256(args.csv.read_bytes()).hexdigest()
        repository = AcquisitionRepository(args.db)
        repository.migrate()
        imported = repository.import_pool(args.csv.name, checksum, result.targets)
        print(
            f"pool_id={imported.pool_id} unique_targets={imported.unique_targets} "
            f"source_rows={imported.source_rows} "
            f"duplicates={result.skipped_duplicates} invalid={result.skipped_invalid}"
        )
        return 0
    if args.command == "round-create":
        _require_file(args.db, "database")
        _require_file(args.devices, "device configuration")
        starts_at_ms = _parse_iso8601_ms(args.starts_at)
        config = FleetConfig.from_path(args.devices)
        pool_id = str(args.pool).strip()
        if not pool_id:
            raise SystemExit("pool id is required")
        repository = AcquisitionRepository(args.db)
        if not repository.pool_exists(pool_id):
            raise SystemExit(f"target pool does not exist: {pool_id}")
        repository.migrate()
        round_id = create_exposure_round(
            repository,
            pool_id=pool_id,
            device_seeds={
                device.device_id: device.order_seed for device in config.devices
            },
            starts_at_ms=starts_at_ms,
        )
        print(
            f"round_id={round_id} assignments={repository.assignment_count(round_id)} "
            f"devices={len(config.devices)}"
        )
        return 0
    if args.command == "fleet-run":
        _require_file(args.db, "database")
        _require_file(args.devices, "device configuration")
        round_id = str(args.round).strip()
        if not round_id:
            raise SystemExit("round id is required")
        config = FleetConfig.from_path(args.devices)
        repository = AcquisitionRepository(args.db)
        if not repository.round_exists(round_id):
            raise SystemExit(f"round does not exist: {round_id}") from None
        round_device_ids = repository.round_device_ids(round_id)
        configured_device_ids = tuple(
            sorted(device.device_id for device in config.devices)
        )
        if configured_device_ids != round_device_ids:
            raise SystemExit("device ids do not match round")
        repository.migrate()
        return _run_acquisition_fleet(repository, round_id, config)
    if args.command == "assignment-retry":
        _require_file(args.db, "database")
        if args.assignment <= 0:
            raise SystemExit("assignment id must be positive")
        repository = AcquisitionRepository(args.db)
        if not repository.assignment_exists(args.assignment):
            raise SystemExit(f"assignment does not exist: {args.assignment}")
        repository.migrate()
        try:
            assignment = repository.retry_assignment(args.assignment)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        print(
            f"assignment_id={assignment.assignment_id} "
            f"phase={assignment.phase.value} retry_ready=true"
        )
        return 0
    if args.command == "capacity":
        _require_file(args.db, "database")
        round_id = str(args.round).strip()
        if not round_id:
            raise SystemExit("round id is required")
        if args.expected_devices <= 0:
            raise SystemExit("expected device count must be positive")
        if args.target_count <= 0:
            raise SystemExit("target count must be positive")
        if args.effective_hours <= 0:
            raise SystemExit("effective hours must be positive")
        repository = AcquisitionRepository(args.db)
        if not repository.round_exists(round_id):
            raise SystemExit(f"round does not exist: {round_id}")
        try:
            report = evaluate_round_capacity(
                repository,
                round_id,
                expected_devices=args.expected_devices,
                target_count=args.target_count,
                effective_hours=args.effective_hours,
            )
        except KeyError:
            raise SystemExit(f"round does not exist: {round_id}") from None
        if args.json_output:
            print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
        else:
            print(
                f"measured_seconds={report.measured_seconds:.3f} "
                f"projected_unique_per_day={report.projected_unique_per_day} "
                f"slowest_device_id={report.slowest_device_id or 'none'} "
                f"fully_covered_targets={report.fully_covered_targets} "
                f"uncertain_count={report.uncertain_count} "
                f"passed={str(report.passed).lower()}"
            )
            if report.reasons:
                print("reasons=" + "; ".join(report.reasons))
        return 0 if report.passed else 1
    if args.command == "validate":
        result = read_targets(args.csv)
        print(
            f"targets={len(result.targets)} duplicates={result.skipped_duplicates} "
            f"invalid={result.skipped_invalid}"
        )
        return 0
    if args.command == "import":
        result = read_targets(args.csv)
        database = Database(args.db)
        database.migrate()
        batch_id = hashlib.sha256(args.csv.read_bytes()).hexdigest()[:16]
        for target in result.targets:
            if args.device_id:
                database.assign_target_to_devices(
                    batch_id,
                    target.target_id,
                    target.username,
                    tuple(args.device_id),
                    profile_metrics=target.profile_metrics,
                    private_account=target.private_account,
                    sec_uid=target.sec_uid,
                    profile_url=target.profile_url,
                )
            else:
                database.insert_task(
                    batch_id,
                    target.target_id,
                    target.username,
                    profile_metrics=target.profile_metrics,
                    private_account=target.private_account,
                    sec_uid=target.sec_uid,
                    profile_url=target.profile_url,
                )
        print(
            f"imported={len(result.targets)} duplicates={result.skipped_duplicates} "
            f"invalid={result.skipped_invalid}"
        )
        return 0
    if args.command in {"serve", "dashboard"}:
        import uvicorn

        from .api import create_app
        from .web_accounts import WebAccountRegistry

        _load_env_file(args.env_file)
        configured_accounts = args.web_accounts or _path_from_environment(
            "TIKPOC_WEB_ACCOUNTS"
        )
        registry = (
            WebAccountRegistry.from_path(configured_accounts)
            if configured_accounts is not None
            else None
        )
        app = create_app(
            args.db,
            registry=registry,
            tiktok_app_secret=os.getenv("TIKPOC_TIKTOK_APP_SECRET", ""),
            webhook_max_age_seconds=int(
                os.getenv("TIKPOC_WEBHOOK_MAX_AGE_SECONDS", "300")
            ),
        )
        if args.with_web_worker:
            if registry is None:
                raise SystemExit(
                    "--with-web-worker requires --web-accounts or TIKPOC_WEB_ACCOUNTS"
                )
            from .web_worker import start_web_worker_thread

            start_web_worker_thread(
                args.db,
                registry=registry,
                idle_sleep_seconds=args.web_worker_idle_sleep,
            )
        print(f"console=http://{args.host}:{args.port}", flush=True)
        try:
            uvicorn.run(app, host=args.host, port=args.port)
        except KeyboardInterrupt:
            pass
        return 0
    if args.command == "web-worker":
        from .web_accounts import WebAccountRegistry
        from .web_worker import run_web_queue

        _load_env_file(args.env_file)
        configured_accounts = args.web_accounts or _path_from_environment(
            "TIKPOC_WEB_ACCOUNTS"
        )
        if configured_accounts is None:
            raise SystemExit(
                "web-worker requires --web-accounts or TIKPOC_WEB_ACCOUNTS"
            )
        try:
            run_web_queue(
                args.db,
                registry=WebAccountRegistry.from_path(configured_accounts),
                idle_sleep_seconds=args.idle_sleep,
                once=args.once,
            )
        except KeyboardInterrupt:
            return 130
        return 0
    if args.command == "run":
        from .runner import run_queue

        action_policies = {}
        for action in ("like", "favorite", "share"):
            probability = getattr(args, f"{action}_probability")
            hourly_limit = getattr(args, f"{action}_hourly_limit")
            action_policies[action] = ActionPolicy(
                enabled=probability > 0 and hourly_limit > 0,
                probability=probability,
                hourly_limit=hourly_limit,
            )
        try:
            run_queue(
                args.db,
                args.appium_url,
                args.udid,
                interaction_policy=InteractionPolicy(
                    **action_policies,
                    trace_probability=args.trace_probability,
                ),
                device_id=args.device_id,
                event_driven=args.event_driven,
            )
        except KeyboardInterrupt:
            return 130
        return 0
    database = Database(args.db)
    database.migrate()
    counts = database.count_by_state()
    print(" ".join(f"{state}={count}" for state, count in counts.items()) or "empty")
    return 0


def _path_from_environment(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).expanduser() if value else None


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} does not exist: {path}")


def _parse_iso8601_ms(value: str) -> int:
    normalized = str(value).strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit("starts-at must be a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit("starts-at must include a timezone")
    return int(parsed.timestamp() * 1_000)


def _run_acquisition_fleet(
    repository: AcquisitionRepository, round_id: str, config: FleetConfig
) -> int:
    from .fleet_runtime import run_acquisition_fleet

    return run_acquisition_fleet(repository, round_id, config)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key.replace("_", "a").isalnum() or key[0].isdigit():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


if __name__ == "__main__":
    raise SystemExit(main())

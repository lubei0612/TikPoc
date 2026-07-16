import argparse
import hashlib
import os
from pathlib import Path
from typing import Sequence

from .db import Database
from .importer import read_targets
from .interactions import ActionPolicy, InteractionPolicy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tikpoc")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("csv", type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("csv", type=Path)
    import_command.add_argument("--db", type=Path, required=True)
    import_command.add_argument("--device-id", action="append", default=[])
    status = commands.add_parser("status")
    status.add_argument("--db", type=Path, required=True)
    dashboard = commands.add_parser("dashboard")
    dashboard.add_argument("--db", type=Path, required=True)
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--web-accounts", type=Path)
    dashboard.add_argument("--env-file", type=Path, default=Path(".env.local"))
    dashboard.add_argument("--with-web-worker", action="store_true")
    dashboard.add_argument("--web-worker-idle-sleep", type=float, default=1.0)
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
    if args.command == "dashboard":
        from .dashboard import create_server
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
        server = create_server(
            args.db,
            args.host,
            args.port,
            web_account_registry=registry,
            tiktok_app_secret=os.getenv("TIKPOC_TIKTOK_APP_SECRET", ""),
            webhook_max_age_seconds=int(
                os.getenv("TIKPOC_WEBHOOK_MAX_AGE_SECONDS", "300")
            ),
        )
        if args.with_web_worker:
            if registry is None:
                raise SystemExit(
                    "--with-web-worker requires --web-accounts or "
                    "TIKPOC_WEB_ACCOUNTS"
                )
            from .web_worker import start_web_worker_thread

            start_web_worker_thread(
                args.db,
                registry=registry,
                idle_sleep_seconds=args.web_worker_idle_sleep,
            )
        print(f"dashboard=http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
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

import argparse
import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

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
    priority_import = commands.add_parser("priority-import")
    priority_import.add_argument("--db", type=Path, required=True)
    priority_import.add_argument("--devices", type=Path, required=True)
    priority_import.add_argument("--file", type=Path, required=True)
    priority_import.add_argument("--source-live", required=True)
    priority_status = commands.add_parser("priority-status")
    priority_status.add_argument("--db", type=Path, required=True)
    supabase_pool_import = commands.add_parser("supabase-pool-import")
    supabase_pool_import.add_argument("--csv", type=Path, required=True)
    supabase_pool_import.add_argument(
        "--env-file",
        type=Path,
        default=Path("config/secrets/supabase.env"),
    )
    proxy_guard = commands.add_parser("proxy-guard")
    proxy_guard.add_argument("--devices", type=Path, required=True)
    proxy_guard.add_argument("--adb-path", type=Path)
    proxy_guard.add_argument("--interval", type=float, default=30.0)
    proxy_guard.add_argument("--once", action="store_true")
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
    browser = commands.add_parser("browser")
    browser_commands = browser.add_subparsers(dest="browser_command", required=True)
    browser_status = browser_commands.add_parser("status")
    browser_status.add_argument("--dashboard-url", default="http://127.0.0.1:8766")
    browser_status.add_argument("--json", action="store_true", dest="json_output")
    browser_connect = browser_commands.add_parser("connect")
    browser_connect.add_argument("--web-accounts", type=Path, required=True)
    browser_connect.add_argument("--dashboard-url", default="http://127.0.0.1:8766")
    browser_connect.add_argument("--timeout", type=float, default=60.0)
    browser_connect.add_argument("--poll-interval", type=float, default=1.0)
    browser_guide = browser_commands.add_parser("guide")
    browser_guide.add_argument("--extension-path", type=Path)
    catalog = commands.add_parser("catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_scrape = catalog_commands.add_parser("scrape")
    catalog_scrape.add_argument("--shop", required=True)
    catalog_scrape.add_argument("--output", type=Path, required=True)
    catalog_scrape.add_argument("--max-products", type=int)
    catalog_scrape.add_argument("--page-size", type=int, default=50)
    catalog_scrape.add_argument("--delay", type=float, default=0.5)
    catalog_scrape.add_argument("--no-images", action="store_true")
    catalog_scrape.add_argument("--max-image-mb", type=int, default=25)
    catalog_select = catalog_commands.add_parser("select")
    catalog_select.add_argument("--manifest", type=Path, required=True)
    catalog_select.add_argument("--signals", type=Path, required=True)
    catalog_select.add_argument("--output", type=Path, required=True)
    catalog_select.add_argument("--limit", type=int, default=20)
    catalog_prepare = catalog_commands.add_parser("prepare")
    catalog_prepare.add_argument("--manifest", type=Path, required=True)
    catalog_prepare.add_argument("--db", type=Path, required=True)
    catalog_prepare.add_argument("--account-id", action="append", required=True)
    catalog_prepare.add_argument("--output", type=Path, required=True)
    catalog_prepare.add_argument("--settings", type=Path)
    catalog_publish = catalog_commands.add_parser("publish")
    catalog_publish.add_argument("--db", type=Path, required=True)
    catalog_publish.add_argument("--devices", type=Path, required=True)
    catalog_publish.add_argument("--device-id", required=True)
    catalog_publish.add_argument("--expected-username", required=True)
    catalog_publish.add_argument("--adb-path", type=Path)
    catalog_publish.add_argument("--max-posts", type=int, default=1)
    catalog_run = catalog_commands.add_parser("run")
    catalog_run.add_argument("--shop", required=True)
    catalog_run.add_argument("--catalog-output", type=Path, required=True)
    catalog_run.add_argument("--db", type=Path, required=True)
    catalog_run.add_argument("--devices", type=Path, required=True)
    catalog_run.add_argument("--device-id", required=True)
    catalog_run.add_argument("--expected-username", required=True)
    catalog_run.add_argument("--settings", type=Path)
    catalog_run.add_argument("--adb-path", type=Path)
    catalog_run.add_argument("--max-products", type=int)
    catalog_run.add_argument("--page-size", type=int, default=50)
    catalog_run.add_argument("--delay", type=float, default=0.5)
    catalog_run.add_argument("--max-image-mb", type=int, default=25)
    catalog_run.add_argument("--max-posts", type=int, default=1)
    catalog_status = catalog_commands.add_parser("status")
    catalog_status.add_argument("--db", type=Path, required=True)
    catalog_status.add_argument("--account-id", default="")
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
    if args.command in {"priority-import", "priority-status"}:
        from .priority_service import PriorityBatchService, summary_json

        _require_file(args.db, "database")
        repository = AcquisitionRepository(args.db)
        repository.migrate()
        service = PriorityBatchService(repository)
        try:
            if args.command == "priority-import":
                _require_file(args.devices, "device configuration")
                _require_file(args.file, "priority input")
                summary = service.import_batch(
                    args.file,
                    source_live_id=args.source_live,
                    fleet_config=FleetConfig.from_path(args.devices),
                )
                payload = summary_json(summary)
            else:
                payload = service.status()
        except (KeyError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    if args.command == "catalog":
        if args.catalog_command in {"scrape", "run"}:
            if args.max_products is not None and args.max_products <= 0:
                raise SystemExit("max-products must be positive")
            if not 1 <= args.page_size <= 100:
                raise SystemExit("page-size must be between 1 and 100")
            if args.delay < 0:
                raise SystemExit("delay must be non-negative")
            if args.max_image_mb <= 0:
                raise SystemExit("max-image-mb must be positive")
        if args.catalog_command in {"publish", "run"} and args.max_posts <= 0:
            raise SystemExit("max-posts must be positive")
        if args.catalog_command == "select" and args.limit <= 0:
            raise SystemExit("limit must be positive")
        try:
            if args.catalog_command == "scrape":
                result = _run_catalog_scrape(
                    shop=args.shop,
                    output_dir=args.output,
                    max_products=args.max_products,
                    page_size=args.page_size,
                    delay_seconds=args.delay,
                    download_images=not args.no_images,
                    max_image_bytes=args.max_image_mb * 1024 * 1024,
                )
                print(
                    f"products={result.product_count} images={result.image_count} "
                    f"failed_images={result.failed_image_count} output={args.output}"
                )
                return 0
            if args.catalog_command == "select":
                selected = _run_catalog_select(
                    manifest=args.manifest,
                    signals=args.signals,
                    output=args.output,
                    limit=args.limit,
                )
                print(f"selected={selected} output={args.output}")
                return 0
            if args.catalog_command == "prepare":
                jobs = _run_catalog_prepare(
                    manifest=args.manifest,
                    database_path=args.db,
                    account_ids=tuple(args.account_id),
                    output_dir=args.output,
                    settings_path=args.settings,
                )
                print(f"prepared={len(jobs)} database={args.db}")
                return 0
            if args.catalog_command == "publish":
                jobs = _run_catalog_publish(
                    database_path=args.db,
                    devices_path=args.devices,
                    device_id=args.device_id,
                    expected_username=args.expected_username,
                    adb_path=args.adb_path,
                    max_posts=args.max_posts,
                )
                print(_catalog_publish_summary(jobs))
                return 0 if all(job.state == "published" for job in jobs) else 1
            if args.catalog_command == "run":
                scraped = _run_catalog_scrape(
                    shop=args.shop,
                    output_dir=args.catalog_output,
                    max_products=args.max_products,
                    page_size=args.page_size,
                    delay_seconds=args.delay,
                    download_images=True,
                    max_image_bytes=args.max_image_mb * 1024 * 1024,
                )
                device = _fleet_device(args.devices, args.device_id)
                jobs = _run_catalog_prepare(
                    manifest=args.catalog_output / "manifest.jsonl",
                    database_path=args.db,
                    account_ids=(device.account_id,),
                    output_dir=args.catalog_output / "publishing",
                    settings_path=args.settings,
                )
                published = _run_catalog_publish(
                    database_path=args.db,
                    devices_path=args.devices,
                    device_id=args.device_id,
                    expected_username=args.expected_username,
                    adb_path=args.adb_path,
                    max_posts=args.max_posts,
                )
                print(
                    f"products={scraped.product_count} prepared={len(jobs)} "
                    + _catalog_publish_summary(published)
                )
                return 0 if all(job.state == "published" for job in published) else 1
            if args.catalog_command == "status":
                from collections import Counter

                from .publishing_db import PublishingRepository

                jobs = PublishingRepository(args.db).list_jobs(
                    account_id=args.account_id
                )
                counts = Counter(job.state for job in jobs)
                print(
                    "total="
                    + str(len(jobs))
                    + " "
                    + " ".join(
                        f"{state}={counts[state]}"
                        for state in (
                            "prepared",
                            "approved",
                            "publishing",
                            "published",
                            "uncertain",
                            "rejected",
                        )
                    )
                )
                return 0
        except (OSError, RuntimeError, ValueError) as error:
            raise SystemExit(str(error)) from None
    if args.command == "proxy-guard":
        import time

        _require_file(args.devices, "device configuration")
        if args.interval < 5:
            raise SystemExit("interval must be at least 5 seconds")
        config = FleetConfig.from_path(args.devices)
        try:
            while True:
                rows = _run_proxy_guard(config, args.adb_path)
                for row in rows:
                    print(
                        f"observed_at_ms={row.observed_at_ms} "
                        f"device_id={row.device_id} adb_state={row.adb_state} "
                        f"proxy_state={row.proxy_state} "
                        f"http_state={row.http_state} "
                        f"http_status={row.http_status if row.http_status is not None else '-'}",
                        flush=True,
                    )
                healthy = sum(
                    row.proxy_state in {"healthy", "vpn_healthy"}
                    and row.http_state != "failed"
                    for row in rows
                )
                corrected = sum(
                    row.proxy_state in {"corrected", "vpn_recovered"}
                    and row.http_state != "failed"
                    for row in rows
                )
                failed = len(rows) - healthy - corrected
                http_200 = sum(row.http_status == 200 for row in rows)
                http_unknown = sum(row.http_state == "unknown" for row in rows)
                print(
                    f"devices={len(rows)} healthy={healthy} "
                    f"corrected={corrected} failed={failed} "
                    f"http_200={http_200} http_unknown={http_unknown}",
                    flush=True,
                )
                if args.once:
                    return 0
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 130
    if args.command == "browser":
        from . import browser_connect
        from .web_accounts import WebAccountRegistry

        if args.browser_command == "guide":
            extension_path = (
                args.extension_path or browser_connect.default_extension_path()
            )
            print(f"extension_path={extension_path}")
            print("Chrome: chrome://extensions -> Load unpacked")
            print("Folder dialog: Command+Shift+G -> paste extension_path -> Select")
            print(
                "Then open TikTok and run: tikpoc browser connect --web-accounts CONFIG"
            )
            return 0
        try:
            origin = browser_connect.dashboard_origin(args.dashboard_url)
            if args.browser_command == "status":
                rows = browser_connect.redacted_browser_status(
                    browser_connect.fetch_json(f"{origin}/api/leads")
                )
                if args.json_output:
                    print(json.dumps(rows, sort_keys=True, separators=(",", ":")))
                else:
                    for row in rows:
                        age = row["heartbeat_age_ms"]
                        print(
                            f"account_id={row['account_id']} "
                            f"profile={row['browser_profile_label'] or '-'} "
                            f"role={row['page_role']} "
                            f"expected=@{row['expected_tiktok_username'] or '-'} "
                            f"observed=@{row['observed_username'] or '-'} "
                            f"state={row['binding_state']} "
                            f"heartbeat_age_ms={age if age is not None else '-'}"
                        )
                return 0
            registry = WebAccountRegistry.from_path(args.web_accounts)
            ready, total, _rows = browser_connect.wait_for_browser_health(
                registry,
                origin,
                timeout_seconds=args.timeout,
                poll_interval_seconds=args.poll_interval,
            )
            print(f"ready={ready}/{total}")
            return 0 if ready == total else 1
        except (OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
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
    if args.command == "supabase-pool-import":
        from .supabase_store import SupabaseBusinessStore

        _require_file(args.csv, "CSV file")
        _require_file(args.env_file, "Supabase environment file")
        result = read_targets(args.csv)
        checksum = hashlib.sha256(args.csv.read_bytes()).hexdigest()
        pool_id = f"pool-{checksum[:20]}"
        source_rows = sum(
            max(1, len(target.source_line_numbers)) for target in result.targets
        )
        store = SupabaseBusinessStore.from_env_file(args.env_file)
        store.import_pool(
            pool_id=pool_id,
            source_name=args.csv.name,
            source_checksum=checksum,
            source_rows=source_rows,
            targets=result.targets,
        )
        print(
            f"pool_id={pool_id} unique_targets={len(result.targets)} "
            f"source_rows={source_rows} duplicates={result.skipped_duplicates} "
            f"invalid={result.skipped_invalid}"
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


def _run_proxy_guard(config: FleetConfig, adb_path: Path | None):
    from .proxy_guard import ProxyGuard

    return ProxyGuard(config, adb_path=adb_path).reconcile()


def _run_catalog_scrape(
    *,
    shop: str,
    output_dir: Path,
    max_products: int | None,
    page_size: int,
    delay_seconds: float,
    download_images: bool,
    max_image_bytes: int,
):
    from .catalog import GxhyCatalogClient, parse_gxhy_shop
    from .catalog_export import CatalogExporter

    shop_id, market_code = parse_gxhy_shop(shop)
    products = GxhyCatalogClient().iter_raw_products(
        shop_id=shop_id,
        market_code=market_code,
        page_size=page_size,
        max_products=max_products,
        delay_seconds=delay_seconds,
    )
    return CatalogExporter().export(
        products,
        output_dir=output_dir,
        download_images=download_images,
        max_image_bytes=max_image_bytes,
    )


def _run_catalog_select(
    *, manifest: Path, signals: Path, output: Path, limit: int
) -> int:
    from .catalog_selection import (
        load_manifest_records,
        load_trend_signals,
        select_trending_products,
        write_selected_manifest,
    )

    records = load_manifest_records(manifest)
    selected = select_trending_products(
        records, load_trend_signals(signals), limit=limit
    )
    write_selected_manifest(output, records, selected)
    return len(selected)


def _run_catalog_prepare(
    *,
    manifest: Path,
    database_path: Path,
    account_ids: tuple[str, ...],
    output_dir: Path,
    settings_path: Path | None,
):
    import time

    from .catalog_workflow import (
        CatalogSupervisor,
        load_catalog_manifest,
        prepare_catalog_jobs,
    )
    from .publishing_db import PublishingRepository
    from .runtime_settings import RuntimeSettingsStore

    provider_path = settings_path or (
        database_path.parent / "config" / "secrets" / "operator-settings.json"
    )
    provider = RuntimeSettingsStore(provider_path).provider_credentials()
    return prepare_catalog_jobs(
        load_catalog_manifest(manifest),
        repository=PublishingRepository(database_path),
        supervisor=CatalogSupervisor(provider),
        account_ids=account_ids,
        output_dir=output_dir,
        now_ms=int(time.time() * 1000),
    )


def _fleet_device(devices_path: Path, device_id: str):
    config = FleetConfig.from_path(devices_path)
    matches = tuple(
        device for device in config.devices if device.device_id == device_id.strip()
    )
    if len(matches) != 1:
        raise ValueError(f"device id is not configured exactly once: {device_id}")
    return matches[0]


def _run_catalog_publish(
    *,
    database_path: Path,
    devices_path: Path,
    device_id: str,
    expected_username: str,
    adb_path: Path | None,
    max_posts: int,
):
    import os

    from .mobile_catalog_publisher import (
        AdbMediaStager,
        AppiumTikTokPhotoUi,
        MobileCatalogPublisher,
        start_publish_activity,
    )
    from .publishing_db import PublishingRepository
    from .runner import create_driver

    device = _fleet_device(devices_path, device_id)
    repository = PublishingRepository(database_path)
    publisher = MobileCatalogPublisher(
        repository,
        stager=AdbMediaStager(device.adb_endpoint, adb_path=adb_path),
        ui_factory=lambda: AppiumTikTokPhotoUi(
            create_driver(device.appium_url, device.adb_endpoint, command_timeout=60),
            timeout=30,
            activity_opener=lambda: start_publish_activity(
                device.adb_endpoint, adb_path=adb_path
            ),
        ),
    )
    results = []
    owner = f"catalog-publisher-{os.getpid()}"
    for _ in range(max_posts):
        result = publisher.publish_one(
            account_id=device.account_id,
            expected_username=expected_username,
            device_id=device.device_id,
            owner=owner,
        )
        if result is None:
            break
        results.append(result)
        if result.state != "published":
            break
    return tuple(results)


def _catalog_publish_summary(jobs) -> str:
    published = sum(job.state == "published" for job in jobs)
    uncertain = sum(job.state == "uncertain" for job in jobs)
    return f"attempted={len(jobs)} published={published} uncertain={uncertain}"


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

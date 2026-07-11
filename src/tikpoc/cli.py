import argparse
import hashlib
from pathlib import Path
from typing import Sequence

from .db import Database
from .importer import read_targets


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tikpoc")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("csv", type=Path)
    import_command = commands.add_parser("import")
    import_command.add_argument("csv", type=Path)
    import_command.add_argument("--db", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--db", type=Path, required=True)
    dashboard = commands.add_parser("dashboard")
    dashboard.add_argument("--db", type=Path, required=True)
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    run = commands.add_parser("run")
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--appium-url", default="http://127.0.0.1:4723")
    run.add_argument("--udid", default="emulator-5554")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate":
        result = read_targets(args.csv)
        print(f"targets={len(result.targets)} duplicates={result.skipped_duplicates}")
        return 0
    if args.command == "import":
        result = read_targets(args.csv)
        database = Database(args.db)
        database.migrate()
        batch_id = hashlib.sha256(args.csv.read_bytes()).hexdigest()[:16]
        for target in result.targets:
            database.insert_task(batch_id, target.target_id, target.username)
        print(f"imported={len(result.targets)} duplicates={result.skipped_duplicates}")
        return 0
    if args.command == "dashboard":
        from .dashboard import create_server

        server = create_server(args.db, args.host, args.port)
        print(f"dashboard=http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    if args.command == "run":
        from .runner import run_queue

        run_queue(args.db, args.appium_url, args.udid)
        return 0
    database = Database(args.db)
    database.migrate()
    counts = database.count_by_state()
    print(" ".join(f"{state}={count}" for state, count in counts.items()) or "empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

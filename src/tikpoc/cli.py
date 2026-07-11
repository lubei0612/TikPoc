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
    database = Database(args.db)
    database.migrate()
    counts = database.count_by_state()
    print(" ".join(f"{state}={count}" for state, count in counts.items()) or "empty")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

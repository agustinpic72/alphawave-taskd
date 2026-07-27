import argparse
from pathlib import Path

from app.core.db import SessionLocal, init_db
from app.services import backups
from app.services.system import format_preflight_text, preflight, restore_database_from_backup


RESTORE_CONFIRMATION_PHRASE = "RESTAURAR BACKUP"


def main() -> None:
    parser = argparse.ArgumentParser(prog="alphawave-taskd")
    subcommands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = subcommands.add_parser("preflight")
    preflight_parser.add_argument("--strict", action="store_true")
    subcommands.add_parser("backup")
    restore_parser = subcommands.add_parser("restore")
    restore_parser.add_argument("backup_path")
    restore_parser.add_argument(
        "--confirm",
        required=True,
        help=f'Explicit confirmation; must be exactly "{RESTORE_CONFIRMATION_PHRASE}".',
    )

    args = parser.parse_args()
    if args.command == "preflight":
        init_db()
        report = preflight(strict=args.strict)
        print(format_preflight_text(report))
        raise SystemExit(0 if report.status in {"ok", "warning"} else 1)
    if args.command == "backup":
        init_db()
        path = backups.create_backup()
        if not path:
            print("No backup created. Check BACKUP_ENABLED and DATABASE_URL.")
            raise SystemExit(1)
        print(f"Created backup: {path}")
        return
    if args.command == "restore":
        if args.confirm != RESTORE_CONFIRMATION_PHRASE:
            parser.error(f'--confirm must be exactly "{RESTORE_CONFIRMATION_PHRASE}"')
        init_db()
        safety = restore_database_from_backup(
            Path(args.backup_path),
            create_safety_backup=lambda: backups.create_backup(source="pre_restore"),
        )
        if safety:
            print(f"Safety backup: {safety}")
        print("Restore completed.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""POSIX metadata and durable replacement helpers for runtime rollback."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from pathlib import Path

import systemd_ownership


def _validate_database(path: Path) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise SystemExit("SQLite integrity check failed")


def capture_metadata(database: Path, output: Path) -> None:
    metadata = systemd_ownership.capture_metadata(database)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(metadata.to_json() + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        systemd_ownership.fsync_directory(output.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_replace(source: Path, target: Path, metadata_path: Path) -> None:
    _validate_database(source)
    metadata = systemd_ownership.OwnershipSnapshot.from_json(
        metadata_path.read_text(encoding="utf-8")
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.rollback-{os.getpid()}"
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        _validate_database(temporary)
        systemd_ownership.prepare_sqlite_replacement(
            temporary, metadata, restore_parent=True, writable=True
        )
        systemd_ownership.atomic_replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    capture = commands.add_parser("capture-metadata")
    capture.add_argument("--database", required=True)
    capture.add_argument("--output", required=True)

    replace = commands.add_parser("atomic-replace")
    replace.add_argument("--source", required=True)
    replace.add_argument("--target", required=True)
    replace.add_argument("--metadata", required=True)

    args = parser.parse_args()
    if args.command == "capture-metadata":
        capture_metadata(Path(args.database), Path(args.output))
    else:
        atomic_replace(Path(args.source), Path(args.target), Path(args.metadata))


if __name__ == "__main__":
    main()

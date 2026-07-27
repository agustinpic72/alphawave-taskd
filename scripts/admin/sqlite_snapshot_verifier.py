#!/usr/bin/env python3
"""Exhaustive, privacy-safe verification for SQLite migration snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_FORMAT_VERSION = 1


class SnapshotVerificationError(RuntimeError):
    """Raised when a snapshot is invalid or a source/destination pair differs."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_sql(sql: str | None) -> str:
    if not sql:
        return ""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()


def _digest_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _open_snapshot(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SnapshotVerificationError("SQLite snapshot is not a regular file")
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _schema_objects(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name, tbl_name
        """
    ).fetchall()
    return [
        {
            "type": str(object_type),
            "name": str(name),
            "table": str(table),
            "sql": _normalized_sql(sql),
        }
        for object_type, name, table, sql in rows
    ]


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    return {
        name: int(connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(name)}").fetchone()[0])
        for name in names
    }


def _index_metadata(connection: sqlite3.Connection, tables: list[str]) -> list[dict[str, Any]]:
    sql_by_name = {
        str(name): sql
        for name, sql in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index'"
        )
    }
    indexes: list[dict[str, Any]] = []
    for table in tables:
        for _, name, unique, origin, partial in connection.execute(
            f"PRAGMA index_list({_quote_identifier(table)})"
        ):
            columns = [
                column_name
                for _, _, column_name, _, _, key in connection.execute(
                    f"PRAGMA index_xinfo({_quote_identifier(str(name))})"
                )
                if key
            ]
            normalized_sql = _normalized_sql(sql_by_name.get(str(name)))
            indexes.append(
                {
                    "name": str(name),
                    "table": table,
                    "unique": bool(unique),
                    "origin": str(origin),
                    "partial": bool(partial),
                    "columns": columns,
                    "sql_sha256": hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest(),
                }
            )
    return sorted(indexes, key=lambda item: (item["table"], item["name"]))


def _trigger_metadata(connection: sqlite3.Connection) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT name, tbl_name, sql
        FROM sqlite_master
        WHERE type = 'trigger' AND name NOT LIKE 'sqlite_%'
        ORDER BY tbl_name, name
        """
    ).fetchall()
    return [
        {
            "name": str(name),
            "table": str(table),
            "sql_sha256": hashlib.sha256(_normalized_sql(sql).encode("utf-8")).hexdigest(),
        }
        for name, table, sql in rows
    ]


def inspect_snapshot(path: Path | str) -> dict[str, Any]:
    """Inspect an immutable SQLite snapshot without exposing row content or its path."""
    snapshot = Path(path)
    hash_before = sha256_file(snapshot)
    try:
        with _open_snapshot(snapshot) as connection:
            integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            schema = _schema_objects(connection)
            table_counts = _table_counts(connection)
            tables = list(table_counts)
            indexes = _index_metadata(connection, tables)
            triggers = _trigger_metadata(connection)
            report = {
                "sha256": hash_before,
                "size_bytes": snapshot.stat().st_size,
                "integrity_check": {
                    "status": "ok" if integrity_rows == ["ok"] else "failed",
                    "result_count": len(integrity_rows),
                },
                "foreign_key_check": {
                    "status": "ok" if not foreign_key_rows else "failed",
                    "violation_count": len(foreign_key_rows),
                },
                "schema_version": int(connection.execute("PRAGMA schema_version").fetchone()[0]),
                "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
                "application_id": int(connection.execute("PRAGMA application_id").fetchone()[0]),
                "table_counts": table_counts,
                "schema_digest": _digest_json(schema),
                "indexes": {"count": len(indexes), "items": indexes},
                "triggers": {"count": len(triggers), "items": triggers},
            }
    except sqlite3.Error as exc:
        raise SnapshotVerificationError(f"Unable to inspect SQLite snapshot: {exc}") from exc

    if sha256_file(snapshot) != hash_before:
        raise SnapshotVerificationError("SQLite snapshot changed while it was being inspected")
    return report


def compare_reports(
    source: dict[str, Any],
    destination: dict[str, Any],
    *,
    require_exact_hash: bool = True,
) -> dict[str, Any]:
    """Compare two inspection reports and return privacy-safe mismatch field names."""
    fields = [
        "integrity_check",
        "foreign_key_check",
        "schema_version",
        "user_version",
        "application_id",
        "table_counts",
        "schema_digest",
        "indexes",
        "triggers",
    ]
    mismatches = [field for field in fields if source[field] != destination[field]]
    if source["integrity_check"]["status"] != "ok":
        mismatches.append("source_integrity")
    if destination["integrity_check"]["status"] != "ok":
        mismatches.append("destination_integrity")
    if source["foreign_key_check"]["status"] != "ok":
        mismatches.append("source_foreign_keys")
    if destination["foreign_key_check"]["status"] != "ok":
        mismatches.append("destination_foreign_keys")
    if require_exact_hash and source["sha256"] != destination["sha256"]:
        mismatches.append("sha256")
    unique_mismatches = sorted(set(mismatches))
    return {
        "matched": not unique_mismatches,
        "exact_hash_required": require_exact_hash,
        "mismatches": unique_mismatches,
    }


def compare_snapshots(
    source: Path | str,
    destination: Path | str,
    *,
    require_exact_hash: bool = True,
) -> dict[str, Any]:
    source_report = inspect_snapshot(source)
    destination_report = inspect_snapshot(destination)
    return {
        "source": source_report,
        "destination": destination_report,
        "comparison": compare_reports(
            source_report,
            destination_report,
            require_exact_hash=require_exact_hash,
        ),
    }


def verify_snapshots(
    source: Path | str,
    destination: Path | str,
    *,
    require_exact_hash: bool = True,
) -> dict[str, Any]:
    result = compare_snapshots(source, destination, require_exact_hash=require_exact_hash)
    if not result["comparison"]["matched"]:
        fields = ", ".join(result["comparison"]["mismatches"])
        raise SnapshotVerificationError(f"SQLite snapshots differ: {fields}")
    return result


def verify_snapshot(snapshot: Path | str) -> dict[str, Any]:
    report = inspect_snapshot(snapshot)
    failures = []
    if report["integrity_check"]["status"] != "ok":
        failures.append("integrity_check")
    if report["foreign_key_check"]["status"] != "ok":
        failures.append("foreign_key_check")
    if failures:
        raise SnapshotVerificationError(
            "SQLite snapshot validation failed: " + ", ".join(failures)
        )
    return report


def verify_schema_compatible(reference: Path | str, candidate: Path | str) -> dict[str, Any]:
    reference_report = verify_snapshot(reference)
    candidate_report = verify_snapshot(candidate)
    fields = ("user_version", "application_id", "schema_digest", "indexes", "triggers")
    mismatches = [field for field in fields if reference_report[field] != candidate_report[field]]
    if set(reference_report["table_counts"]) != set(candidate_report["table_counts"]):
        mismatches.append("table_set")
    payload = {
        "matched": not mismatches,
        "mismatches": sorted(mismatches),
        "reference_schema_digest": reference_report["schema_digest"],
        "candidate_schema_digest": candidate_report["schema_digest"],
        "reference_table_count": len(reference_report["table_counts"]),
        "candidate_table_count": len(candidate_report["table_counts"]),
    }
    if mismatches:
        raise SnapshotVerificationError(
            "SQLite candidate schema differs from the expected runtime schema: "
            + ", ".join(sorted(mismatches))
        )
    return payload


def build_verification_manifest(
    source: Path | str,
    destination: Path | str,
    *,
    require_exact_hash: bool = True,
) -> dict[str, Any]:
    """Build a JSON-safe manifest containing no paths and no database row values."""
    result = compare_snapshots(source, destination, require_exact_hash=require_exact_hash)
    return {
        "format_version": MANIFEST_FORMAT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": result["source"],
        "destination_snapshot": result["destination"],
        "comparison": result["comparison"],
    }


def _write_json(payload: dict[str, Any], output: str | None) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    inspect_command = commands.add_parser("inspect", help="inspect one immutable snapshot")
    inspect_command.add_argument("snapshot")
    inspect_command.add_argument("--output")

    verify_command = commands.add_parser("verify", help="fail unless one snapshot is valid")
    verify_command.add_argument("snapshot")
    verify_command.add_argument("--output")

    schema_command = commands.add_parser(
        "schema-compare", help="compare candidate schema against a trusted runtime database"
    )
    schema_command.add_argument("reference")
    schema_command.add_argument("candidate")
    schema_command.add_argument("--output")

    compare_command = commands.add_parser("compare", help="compare source and destination snapshots")
    compare_command.add_argument("source")
    compare_command.add_argument("destination")
    compare_command.add_argument("--output")
    compare_command.add_argument("--allow-different-hash", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "inspect":
            payload = inspect_snapshot(args.snapshot)
        elif args.command == "verify":
            payload = verify_snapshot(args.snapshot)
        elif args.command == "schema-compare":
            payload = verify_schema_compatible(args.reference, args.candidate)
        else:
            payload = build_verification_manifest(
                args.source,
                args.destination,
                require_exact_hash=not args.allow_different_hash,
            )
            if not payload["comparison"]["matched"]:
                _write_json(payload, args.output)
                raise SystemExit(1)
        _write_json(payload, args.output)
    except SnapshotVerificationError as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()

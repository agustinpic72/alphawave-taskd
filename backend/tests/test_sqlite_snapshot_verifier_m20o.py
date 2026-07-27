import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "admin" / "sqlite_snapshot_verifier.py"
SPEC = importlib.util.spec_from_file_location("sqlite_snapshot_verifier", MODULE_PATH)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _create_database(path, *, secret="private task content", extra_table=True, schema_objects=True):
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA user_version = 42")
        connection.execute("PRAGMA application_id = 1234")
        connection.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id), title TEXT)"
        )
        if extra_table:
            connection.execute('CREATE TABLE "odd table" (id INTEGER PRIMARY KEY, note TEXT)')
        if schema_objects:
            connection.execute("CREATE INDEX tasks_project_idx ON tasks(project_id)")
            connection.execute(
                "CREATE TRIGGER tasks_insert AFTER INSERT ON tasks BEGIN UPDATE projects SET name = name WHERE id = NEW.project_id; END"
            )
        connection.execute("INSERT INTO projects VALUES (1, ?)", (secret,))
        connection.execute("INSERT INTO tasks VALUES (1, 1, ?)", (secret,))


def test_inspection_is_exhaustive_and_contains_no_row_content_or_path(tmp_path):
    database = tmp_path / "secret-database-name.sqlite"
    secret = "highly-sensitive-row-value"
    _create_database(database, secret=secret)

    report = verifier.inspect_snapshot(database)
    serialized = json.dumps(report, sort_keys=True)

    assert report["sha256"] == verifier.sha256_file(database)
    assert report["integrity_check"] == {"status": "ok", "result_count": 1}
    assert report["foreign_key_check"] == {"status": "ok", "violation_count": 0}
    assert report["user_version"] == 42
    assert report["application_id"] == 1234
    assert report["table_counts"] == {"odd table": 0, "projects": 1, "tasks": 1}
    assert report["indexes"]["count"] == 1
    assert report["indexes"]["items"][0]["columns"] == ["project_id"]
    assert report["triggers"]["count"] == 1
    assert len(report["schema_digest"]) == 64
    assert secret not in serialized
    assert database.name not in serialized
    assert "path" not in serialized.lower()


def test_exact_snapshot_copy_passes_every_comparison(tmp_path):
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "destination.sqlite"
    _create_database(source)
    shutil.copyfile(source, destination)

    result = verifier.verify_snapshots(source, destination)

    assert result["comparison"] == {
        "matched": True,
        "exact_hash_required": True,
        "mismatches": [],
    }
    assert result["source"]["schema_digest"] == result["destination"]["schema_digest"]


def test_row_count_and_exact_hash_mismatch_fail_closed(tmp_path):
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "destination.sqlite"
    _create_database(source)
    shutil.copyfile(source, destination)
    with sqlite3.connect(destination) as connection:
        connection.execute("INSERT INTO tasks VALUES (2, 1, 'different')")

    comparison = verifier.compare_snapshots(source, destination)["comparison"]

    assert comparison["matched"] is False
    assert "sha256" in comparison["mismatches"]
    assert "table_counts" in comparison["mismatches"]
    with pytest.raises(verifier.SnapshotVerificationError, match="sha256.*table_counts|table_counts.*sha256"):
        verifier.verify_snapshots(source, destination)


def test_missing_table_schema_index_and_trigger_mismatches_are_detected(tmp_path):
    source = tmp_path / "source.sqlite"
    destination = tmp_path / "destination.sqlite"
    _create_database(source)
    _create_database(destination, extra_table=False, schema_objects=False)

    mismatches = verifier.compare_snapshots(
        source, destination, require_exact_hash=False
    )["comparison"]["mismatches"]

    assert "table_counts" in mismatches
    assert "schema_digest" in mismatches
    assert "indexes" in mismatches
    assert "triggers" in mismatches


def test_foreign_key_violations_are_counted_without_exposing_values(tmp_path):
    database = tmp_path / "invalid-fk.sqlite"
    _create_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("INSERT INTO tasks VALUES (99, 999, 'do-not-print-this')")

    report = verifier.inspect_snapshot(database)

    assert report["integrity_check"]["status"] == "ok"
    assert report["foreign_key_check"] == {"status": "failed", "violation_count": 1}
    assert "do-not-print-this" not in json.dumps(report)


def test_manifest_is_privacy_safe_and_records_complete_verification(tmp_path):
    source = tmp_path / "source-private.sqlite"
    destination = tmp_path / "destination-private.sqlite"
    secret = "token-like-secret-value"
    _create_database(source, secret=secret)
    shutil.copyfile(source, destination)

    manifest = verifier.build_verification_manifest(source, destination)
    serialized = json.dumps(manifest, sort_keys=True)

    assert manifest["format_version"] == verifier.MANIFEST_FORMAT_VERSION
    assert manifest["comparison"]["matched"] is True
    assert manifest["source_snapshot"]["table_counts"]["tasks"] == 1
    assert manifest["source_snapshot"]["sha256"] == manifest["destination_snapshot"]["sha256"]
    assert secret not in serialized
    assert str(tmp_path) not in serialized
    assert source.name not in serialized


def test_schema_compatibility_rejects_unrelated_valid_database(tmp_path):
    reference = tmp_path / "reference.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _create_database(reference)
    with sqlite3.connect(candidate) as connection:
        connection.execute("CREATE TABLE unrelated_payload (id INTEGER PRIMARY KEY)")

    with pytest.raises(verifier.SnapshotVerificationError, match="expected runtime schema"):
        verifier.verify_schema_compatible(reference, candidate)


def test_schema_compatibility_allows_different_rows_with_same_schema(tmp_path):
    reference = tmp_path / "reference.sqlite"
    candidate = tmp_path / "candidate.sqlite"
    _create_database(reference, secret="old")
    _create_database(candidate, secret="new")
    with sqlite3.connect(candidate) as connection:
        connection.execute("INSERT INTO tasks VALUES (2, 1, 'another row')")

    result = verifier.verify_schema_compatible(reference, candidate)

    assert result["matched"] is True
    assert result["mismatches"] == []

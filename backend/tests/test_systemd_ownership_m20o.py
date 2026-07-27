import importlib.util
import os
import pwd
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "admin" / "systemd_ownership.py"
SPEC = importlib.util.spec_from_file_location("systemd_ownership", MODULE_PATH)
assert SPEC and SPEC.loader
ownership = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ownership
SPEC.loader.exec_module(ownership)


def _database(path: Path, marker: str = "original") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))


def _marker(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT value FROM marker").fetchone()[0]


def test_capture_round_trip_preserves_file_and_parent_metadata(tmp_path):
    directory = tmp_path / "runtime"
    directory.mkdir(mode=0o750)
    database = directory / "app.sqlite"
    _database(database)
    database.chmod(0o640)

    captured = ownership.capture_metadata(database)

    assert captured.file.uid == os.geteuid()
    assert captured.file.gid == os.getegid()
    assert captured.file.mode == 0o640
    assert captured.parent.mode == 0o750
    assert ownership.OwnershipSnapshot.from_json(captured.to_json()) == captured


def test_prepare_converts_modes_probes_writes_and_fsyncs(tmp_path):
    directory = tmp_path / "runtime"
    directory.mkdir(mode=0o750)
    target = directory / "active.sqlite"
    _database(target)
    target.chmod(0o640)
    captured = ownership.capture_metadata(target)

    prepared = directory / ".rollback.sqlite"
    _database(prepared, "replacement")
    prepared.chmod(0o600)
    directory.chmod(0o700)

    ownership.prepare_sqlite_replacement(
        prepared,
        captured,
        restore_parent=True,
        probe_uid=os.geteuid(),
        probe_gid=os.getegid(),
    )

    assert stat.S_IMODE(prepared.stat().st_mode) == 0o640
    assert stat.S_IMODE(directory.stat().st_mode) == 0o750
    with sqlite3.connect(prepared) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = '__alphawave_access_probe'"
        ).fetchone() == (0,)


def test_atomic_replace_is_same_directory_and_keeps_prepared_metadata(tmp_path):
    target = tmp_path / "active.sqlite"
    prepared = tmp_path / ".active.sqlite.prepared"
    _database(target)
    _database(prepared, "replacement")
    prepared.chmod(0o640)

    ownership.atomic_replace(prepared, target)

    assert not prepared.exists()
    assert _marker(target) == "replacement"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    foreign = elsewhere / "candidate.sqlite"
    _database(foreign)
    with pytest.raises(ValueError, match="share a directory"):
        ownership.atomic_replace(foreign, target)


def test_metadata_operations_reject_symlinks(tmp_path):
    database = tmp_path / "active.sqlite"
    _database(database)
    link = tmp_path / "linked.sqlite"
    link.symlink_to(database)

    with pytest.raises(ownership.MetadataError, match="non-symlink regular file"):
        ownership.capture_metadata(link)
    with pytest.raises(ownership.MetadataError, match="cannot safely open"):
        ownership.apply_metadata(link, ownership.capture_metadata(database).file)


def test_unprivileged_process_cannot_claim_another_probe_identity(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can safely switch identities")
    database = tmp_path / "active.sqlite"
    _database(database)

    with pytest.raises(PermissionError, match="requires root"):
        ownership.probe_sqlite_access(database, uid=os.geteuid() + 1, gid=os.getegid())


def test_probe_distinguishes_read_only_from_writable_access(tmp_path):
    database = tmp_path / "read-only.sqlite"
    _database(database)
    database.chmod(0o400)

    if os.geteuid() == 0:
        service = pwd.getpwnam("nobody")
        tmp_path.chmod(0o755)
        database.chmod(0o444)
        uid, gid = service.pw_uid, service.pw_gid
    else:
        uid, gid = os.geteuid(), os.getegid()

    ownership.probe_sqlite_access(database, uid=uid, gid=gid, writable=False)
    with pytest.raises(ownership.SQLiteAccessError, match="access probe failed"):
        ownership.probe_sqlite_access(database, uid=uid, gid=gid, writable=True)


@pytest.mark.skipif(os.geteuid() != 0, reason="requires root to simulate Docker and systemd UIDs")
def test_root_converts_docker_owned_database_to_distinct_service_identity():
    service = pwd.getpwnam("nobody")
    root = Path(tempfile.mkdtemp(prefix="alphawave-ownership-", dir="/tmp"))
    try:
        root.chmod(0o750)
        os.chown(root, service.pw_uid, service.pw_gid)
        target = root / "active.sqlite"
        _database(target)
        os.chown(target, service.pw_uid, service.pw_gid)
        target.chmod(0o600)
        captured = ownership.capture_metadata(target)

        prepared = root / ".rollback.sqlite"
        _database(prepared, "docker")
        docker_uid = 10001 if service.pw_uid != 10001 else 10002
        os.chown(prepared, docker_uid, docker_uid)
        prepared.chmod(0o600)

        ownership.prepare_sqlite_replacement(
            prepared,
            captured,
            probe_uid=service.pw_uid,
            probe_gid=service.pw_gid,
        )
        ownership.atomic_replace(prepared, target)

        details = target.stat()
        assert (details.st_uid, details.st_gid) == (service.pw_uid, service.pw_gid)
        assert stat.S_IMODE(details.st_mode) == 0o600
        ownership.probe_sqlite_access(target, uid=service.pw_uid, gid=service.pw_gid, writable=True)
    finally:
        for child in root.iterdir():
            child.unlink()
        root.rmdir()

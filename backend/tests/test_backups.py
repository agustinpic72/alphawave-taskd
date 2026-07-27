import gzip

from app.services import backups


def test_create_backup_generates_gzip(tmp_path, monkeypatch, minimal_app_db):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sqlite_path = data_dir / "alphawave-taskd.sqlite"
    minimal_app_db(sqlite_path)

    monkeypatch.setattr(backups, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(backups.settings, "database_url", f"sqlite:///{sqlite_path}")
    monkeypatch.setattr(backups.settings, "backup_enabled", True)

    backup_path = backups.create_backup()

    assert backup_path is not None
    assert backup_path.exists()
    with gzip.open(backup_path, "rb") as handle:
        assert handle.read(16) == b"SQLite format 3\x00"

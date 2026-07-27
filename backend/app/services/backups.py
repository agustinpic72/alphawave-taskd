import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.core.db import SessionLocal
from app.models.app_state import AppState
from app.services import settings_service
from app.services.time import utc_now_iso


BACKUP_STATE_KEY = "last_backup_date"
BACKUP_FORMAT_VERSION = 1
BACKUP_ID_PATTERN = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
SQLITE_HEADER = b"SQLite format 3\x00"
MINIMUM_SCHEMA_TABLES = frozenset({"app_state", "tasks"})
MINIMUM_SCHEMA_COLUMNS = {
    "app_state": frozenset({"key", "value"}),
    "tasks": frozenset({"id", "title"}),
}
MAX_METADATA_BYTES = 1024 * 1024
MAX_UNCOMPRESSED_BACKUP_BYTES = 10 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class BackupResult:
    created: bool
    path: Path | None = None
    reason: str | None = None
    backup: dict[str, Any] | None = None


def create_backup(*, db: Session | None = None, manual: bool = True, source: str | None = None) -> Path | None:
    return create_backup_result(db=db, manual=manual, source=source).path


def create_backup_result(*, db: Session | None = None, manual: bool = True, source: str | None = None) -> BackupResult:
    sqlite_path = settings.sqlite_path
    if not sqlite_path:
        return BackupResult(False, reason="DATABASE_URL no apunta a SQLite.")
    if not sqlite_path.exists():
        return BackupResult(False, reason="No encontré el archivo SQLite para respaldar.")
    if not manual and db is not None and not settings_service.backup_settings(db).get("enabled", True):
        return BackupResult(False, reason="Backups automáticos desactivados en Configuración.")
    backup_source = source or ("manual" if manual else "automatic")
    backup_dir = backup_directory()
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return BackupResult(False, reason=f"No se pudo crear el directorio de backups: {exc.strerror or exc}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"alphawave-taskd-{stamp}.sqlite.gz"
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"alphawave-taskd-{stamp}-{counter}.sqlite.gz"
        counter += 1
    snapshot_path: Path | None = None
    compressed_temp_path: Path | None = None
    try:
        snapshot_path = _temporary_path(backup_dir, ".snapshot-", ".sqlite")
        compressed_temp_path = _temporary_path(backup_dir, ".backup-", ".sqlite.gz")
        _create_online_snapshot(sqlite_path, snapshot_path)
        _compress_snapshot(snapshot_path, compressed_temp_path)
        metadata = _new_backup_metadata(
            backup_path,
            compressed_temp_path,
            snapshot_path,
            source=backup_source,
        )
        os.replace(compressed_temp_path, backup_path)
        compressed_temp_path = None
        try:
            _write_metadata(backup_path, metadata)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        _fsync_directory(backup_dir)
        prune_backups(backup_dir, db=db)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return BackupResult(False, reason=f"No se pudo crear backup: {getattr(exc, 'strerror', None) or exc}")
    finally:
        if snapshot_path:
            snapshot_path.unlink(missing_ok=True)
            _remove_sqlite_temp_sidecars(snapshot_path)
        if compressed_temp_path:
            compressed_temp_path.unlink(missing_ok=True)
    return BackupResult(True, path=backup_path, backup=backup_metadata(backup_path))


def latest_backup() -> tuple[Path, str] | None:
    backup_dir = backup_directory()
    if not backup_dir.exists():
        return None
    backups = backup_paths()
    if not backups:
        return None
    latest = backups[0]
    modified = datetime.fromtimestamp(latest.stat().st_mtime, timezone.utc).isoformat()
    return latest, modified


def backup_directory() -> Path:
    return REPO_ROOT / "data" / "backups"


def backup_paths() -> list[Path]:
    backup_dir = backup_directory()
    if not backup_dir.exists():
        return []
    paths = [
        path
        for path in backup_dir.glob("alphawave-taskd-*.sqlite.gz")
        if _is_safe_backup_path(path) and BACKUP_ID_PATTERN.fullmatch(backup_id_from_path(path))
    ]
    return sorted(paths, key=_backup_sort_key, reverse=True)


def list_backups(*, validate: bool = False) -> list[dict[str, Any]]:
    return [backup_metadata(path, validation=validate_backup(path) if validate else None) for path in backup_paths()]


def get_backup(backup_id: str) -> Path | None:
    if not BACKUP_ID_PATTERN.fullmatch(backup_id):
        return None
    path = backup_directory() / f"alphawave-taskd-{backup_id}.sqlite.gz"
    return path if _is_safe_backup_path(path) else None


def backup_id_from_path(path: Path) -> str:
    name = path.name
    if name.startswith("alphawave-taskd-") and name.endswith(".sqlite.gz"):
        return name.removeprefix("alphawave-taskd-").removesuffix(".sqlite.gz")
    return path.stem


def _backup_sort_key(path: Path) -> tuple[float, str, int]:
    backup_id = backup_id_from_path(path)
    parts = backup_id.rsplit("-", 1)
    counter = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() and len(parts[0]) >= 15 else 0
    stamp = parts[0] if counter else backup_id
    return path.stat().st_mtime, stamp, counter


def backup_metadata(path: Path, *, source: str | None = None, db_size_bytes: int | None = None, validation: dict[str, Any] | None = None) -> dict[str, Any]:
    sidecar = _read_metadata(path)
    stat = path.stat()
    validation_payload = validation or sidecar.get("validation") or _default_validation()
    return {
        "id": backup_id_from_path(path),
        "filename": sidecar.get("filename") or path.name,
        "created_at": sidecar.get("created_at") or datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "path_redacted": redact_path(path),
        "size_bytes": stat.st_size,
        "db_size_bytes": db_size_bytes if db_size_bytes is not None else sidecar.get("db_size_bytes"),
        "sha256": sidecar.get("sha256") or _sha256(path),
        "sqlite_sha256": sidecar.get("sqlite_sha256"),
        "format_version": sidecar.get("format_version"),
        "source": source or sidecar.get("source") or "unknown",
        "valid": _validation_passed(validation_payload),
        "validation": validation_payload,
    }


def backup_status_payload(*, db: Session | None = None) -> dict[str, Any]:
    backup_config = settings_service.backup_settings(db) if db is not None else {"enabled": settings.backup_enabled, "retention_days": settings.backup_retention_days}
    backups = list_backups()
    total_size = sum(int(item.get("size_bytes") or 0) for item in backups)
    return {
        "automatic_enabled": bool(backup_config.get("enabled", True)),
        "retention_days": int(backup_config.get("retention_days") or settings.backup_retention_days),
        "backup_dir": redact_path(backup_directory()),
        "latest_backup": backups[0] if backups else None,
        "latest_path": str(backups[0]["path_redacted"]) if backups else None,
        "latest_created_at": backups[0]["created_at"] if backups else None,
        "count": len(backups),
        "total_size_bytes": total_size,
        "last_error": None,
    }


def prune_backups(backup_dir: Path, *, db: Session | None = None) -> None:
    retention_days = settings_service.backup_settings(db).get("retention_days", settings.backup_retention_days) if db is not None else settings.backup_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(retention_days))
    newest = backup_paths()[0] if backup_paths() else None
    for path in backup_dir.glob("alphawave-taskd-*.sqlite.gz"):
        if newest and path == newest:
            continue
        if (_read_metadata(path).get("source") or "unknown") != "automatic":
            continue
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            path.unlink()
            _metadata_path(path).unlink(missing_ok=True)


def run_startup_backup() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with SessionLocal() as db:
        if not settings_service.backup_settings(db).get("enabled", True):
            return
        state = db.get(AppState, BACKUP_STATE_KEY)
        if state and state.value == today:
            return
        backup = create_backup(db=db, manual=False)
        if backup:
            upsert_state(db, BACKUP_STATE_KEY, today)


def upsert_state(db: Session, key: str, value: str) -> None:
    state = db.get(AppState, key)
    if state:
        state.value = value
        state.updated_at = utc_now_iso()
    else:
        db.add(AppState(key=key, value=value, updated_at=utc_now_iso()))
    db.commit()


def validate_backup(path: Path) -> dict[str, Any]:
    checked_at = utc_now_iso()
    failed = {
        "checked_at": checked_at,
        "sqlite_integrity": "failed",
        "readable": False,
        "gzip_valid": False,
        "sqlite_header_valid": False,
        "schema_valid": False,
        "metadata_valid": False,
        "checksum_valid": False,
    }
    if not path.exists():
        return {**failed, "reason": "El backup seleccionado ya no existe."}
    if not _is_safe_backup_path(path):
        return {**failed, "reason": "La ruta del backup no es segura."}
    temp_path: Path | None = None
    try:
        if path.stat().st_size <= 0:
            return {**failed, "readable": True, "reason": "El archivo de backup está vacío."}
        with path.open("rb") as raw:
            if raw.read(2) != b"\x1f\x8b":
                return {**failed, "readable": True, "reason": "El archivo no tiene una cabecera gzip válida."}
        temp_path = _temporary_path(path.parent, ".validate-", ".sqlite")
        _decompress_backup(path, temp_path)
        if _read_header(temp_path) != SQLITE_HEADER:
            return {**failed, "readable": True, "gzip_valid": True, "reason": "El contenido no tiene una cabecera SQLite válida."}
        with _connect_readonly(temp_path) as connection:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            schema_valid = has_minimum_schema(connection)
        integrity_ok = integrity_rows == [("ok",)]
        metadata_valid, checksum_valid, metadata_reason = _validate_metadata(path, temp_path)
        reason = None
        if not integrity_ok:
            reason = "SQLite integrity_check falló."
        elif not schema_valid:
            reason = "El backup no contiene el esquema mínimo requerido."
        elif not metadata_valid or not checksum_valid:
            reason = metadata_reason
        valid = integrity_ok and schema_valid and metadata_valid and checksum_valid
        return {
            "checked_at": checked_at,
            "sqlite_integrity": "ok" if integrity_ok else "failed",
            "readable": True,
            "gzip_valid": True,
            "sqlite_header_valid": True,
            "schema_valid": schema_valid,
            "metadata_valid": metadata_valid,
            "checksum_valid": checksum_valid,
            "reason": None if valid else reason,
        }
    except (OSError, EOFError, gzip.BadGzipFile, sqlite3.DatabaseError, ValueError) as exc:
        return {**failed, "reason": f"No se pudo validar el backup: {exc}"}
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)
            _remove_sqlite_temp_sidecars(temp_path)


def record_validation(path: Path, validation: dict[str, Any]) -> dict[str, Any]:
    metadata = backup_metadata(path, validation=validation)
    _write_metadata(path, metadata)
    return metadata


def prepare_restore_candidate(path: Path, target_path: Path) -> dict[str, Any]:
    validation = validate_backup(path)
    if not _validation_passed(validation):
        raise ValueError(validation.get("reason") or "El backup no superó la validación.")
    _decompress_backup(path, target_path)
    if _read_header(target_path) != SQLITE_HEADER:
        raise ValueError("El contenido restaurado no tiene una cabecera SQLite válida.")
    with _connect_readonly(target_path) as connection:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        schema_valid = has_minimum_schema(connection)
    if integrity_rows != [("ok",)]:
        raise ValueError("SQLite integrity_check falló en el candidato de restore.")
    if not schema_valid:
        raise ValueError("El candidato de restore no contiene el esquema mínimo requerido.")
    metadata_valid, checksum_valid, reason = _validate_metadata(path, target_path)
    if not metadata_valid or not checksum_valid:
        raise ValueError(reason or "La metadata del candidato de restore no es válida.")
    return validation


def restore_plan(path: Path) -> dict[str, Any]:
    validation = validate_backup(path)
    sqlite_path = settings.sqlite_path
    current_db = None
    if sqlite_path:
        current_db = {
            "path_redacted": redact_path(sqlite_path),
            "size_bytes": sqlite_path.stat().st_size if sqlite_path.exists() else 0,
            "last_modified_at": datetime.fromtimestamp(sqlite_path.stat().st_mtime, timezone.utc).isoformat() if sqlite_path.exists() else None,
        }
    backup = backup_metadata(path, validation=validation)
    return {
        "backup": backup,
        "current_db": current_db,
        "actions": [
            "Validar gzip, checksum, integridad SQLite y esquema mínimo.",
            "Crear backup pre-restore de la DB actual.",
            "Preparar la DB restaurada y reemplazar la actual atómicamente.",
            "Revertir al backup pre-restore si falla cualquier comprobación posterior.",
        ],
        "requires_confirmation": True,
        "confirmation_phrase": "RESTAURAR BACKUP",
        "automatic_restore_available": validation.get("sqlite_integrity") == "ok" and all(
            validation.get(key) is True
            for key in ("gzip_valid", "sqlite_header_valid", "schema_valid", "metadata_valid", "checksum_valid")
        ),
        "manual_commands": [
            "systemctl --user stop alphawave-taskd",
            f"./scripts/restore.sh {redact_path(path)}",
            "systemctl --user start alphawave-taskd",
        ],
        "reason": validation.get("reason"),
    }


def redact_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        home = Path.home()
        try:
            return "~/" + str(path.resolve().relative_to(home.resolve()))
        except ValueError:
            return path.name


def _metadata_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".json")


def _read_metadata(path: Path) -> dict[str, Any]:
    metadata_path = _metadata_path(path)
    if not metadata_path.exists():
        return {}
    try:
        if metadata_path.stat().st_size > MAX_METADATA_BYTES:
            return {}
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    metadata_path = _metadata_path(path)
    temp_path = _temporary_path(metadata_path.parent, ".metadata-", ".json")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, metadata_path)
    finally:
        temp_path.unlink(missing_ok=True)


def _default_validation() -> dict[str, Any]:
    return {
        "checked_at": None,
        "sqlite_integrity": "not_checked",
        "readable": True,
        "gzip_valid": None,
        "sqlite_header_valid": None,
        "schema_valid": None,
        "metadata_valid": None,
        "checksum_valid": None,
        "reason": None,
    }


def _validation_passed(validation: dict[str, Any]) -> bool:
    return validation.get("sqlite_integrity") == "ok" and all(
        validation.get(key) is True
        for key in ("gzip_valid", "sqlite_header_valid", "schema_valid", "metadata_valid", "checksum_valid")
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temporary_path(directory: Path, prefix: str, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
    os.close(descriptor)
    return Path(raw_path)


def _create_online_snapshot(source_path: Path, snapshot_path: Path) -> None:
    with sqlite3.connect(str(source_path)) as source, sqlite3.connect(str(snapshot_path)) as target:
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise sqlite3.DatabaseError("SQLite integrity_check failed while creating snapshot")


def _compress_snapshot(snapshot_path: Path, compressed_path: Path) -> None:
    with snapshot_path.open("rb") as source, compressed_path.open("wb") as raw_target:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_target, mtime=0) as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
        raw_target.flush()
        os.fsync(raw_target.fileno())


def _new_backup_metadata(backup_path: Path, compressed_path: Path, snapshot_path: Path, *, source: str) -> dict[str, Any]:
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "id": backup_id_from_path(backup_path),
        "filename": backup_path.name,
        "created_at": utc_now_iso(),
        "source": source,
        "size_bytes": compressed_path.stat().st_size,
        "db_size_bytes": snapshot_path.stat().st_size,
        "sha256": _sha256(compressed_path),
        "sqlite_sha256": _sha256(snapshot_path),
        "validation": _default_validation(),
    }


def _decompress_backup(source_path: Path, target_path: Path) -> None:
    total = 0
    with gzip.open(source_path, "rb") as source, target_path.open("wb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_UNCOMPRESSED_BACKUP_BYTES:
                raise ValueError("El backup supera el tamaño máximo permitido.")
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())


def _read_header(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(len(SQLITE_HEADER))


def _validate_metadata(path: Path, sqlite_path: Path) -> tuple[bool, bool, str | None]:
    metadata_path = _metadata_path(path)
    if not metadata_path.is_file() or metadata_path.is_symlink() or metadata_path.resolve().parent != backup_directory().resolve():
        return False, False, "Falta metadata segura del backup."
    metadata = _read_metadata(path)
    required = {"format_version", "id", "filename", "sha256", "sqlite_sha256", "db_size_bytes"}
    if not required.issubset(metadata):
        return False, False, "Falta metadata obligatoria del backup."
    metadata_valid = (
        metadata.get("format_version") == BACKUP_FORMAT_VERSION
        and metadata.get("id") == backup_id_from_path(path)
        and metadata.get("filename") == path.name
        and metadata.get("db_size_bytes") == sqlite_path.stat().st_size
    )
    if not metadata_valid:
        return False, False, "La metadata del backup no coincide con el archivo."
    checksum_valid = (
        metadata.get("sha256") == _sha256(path)
        and metadata.get("sqlite_sha256") == _sha256(sqlite_path)
    )
    return True, checksum_valid, None if checksum_valid else "El checksum del backup no coincide con la metadata."


def _is_safe_backup_path(path: Path) -> bool:
    try:
        backup_dir = backup_directory().resolve()
        return path.exists() and path.is_file() and not path.is_symlink() and path.resolve().parent == backup_dir
    except OSError:
        return False


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_sqlite_temp_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def has_minimum_schema(connection: sqlite3.Connection) -> bool:
    tables = {
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if not MINIMUM_SCHEMA_TABLES.issubset(tables):
        return False
    for table, required_columns in MINIMUM_SCHEMA_COLUMNS.items():
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
        if not required_columns.issubset(columns):
            return False
    return True

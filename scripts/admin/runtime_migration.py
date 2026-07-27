#!/usr/bin/env python3
"""Small, dependency-free helpers for the systemd/Docker runtime migration scripts."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


LOCAL_DRIVER_MANAGED = "local-driver-managed"
EXTERNAL_BIND_BACKED = "external-bind-backed"
VOLUME_MODES = frozenset({LOCAL_DRIVER_MANAGED, EXTERNAL_BIND_BACKED})
_VOLUME_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]+$")


class VolumeContractError(ValueError):
    """Raised when a volume definition or Docker inspection is unsafe."""


@dataclass(frozen=True)
class VolumeSpec:
    name: str
    mode: str = LOCAL_DRIVER_MANAGED
    device: Path | None = None

    def __post_init__(self) -> None:
        validate_volume_name(self.name)
        if self.mode not in VOLUME_MODES:
            raise VolumeContractError(f"Unsupported volume mode: {self.mode!r}")
        if self.mode == LOCAL_DRIVER_MANAGED:
            if self.device is not None:
                raise VolumeContractError("local-driver-managed volumes cannot define a device")
            return
        if self.device is None:
            raise VolumeContractError("external-bind-backed volumes require a device")
        object.__setattr__(self, "device", validate_device_path(self.device))

    @property
    def expected_options(self) -> dict[str, str]:
        if self.mode == LOCAL_DRIVER_MANAGED:
            return {}
        return {"type": "none", "o": "bind", "device": os.fspath(self.device)}

    def docker_create_args(self) -> list[str]:
        args = ["volume", "create", "--driver", "local"]
        for key, value in self.expected_options.items():
            args.extend(("--opt", f"{key}={value}"))
        args.append(self.name)
        return args


def validate_volume_name(name: str) -> str:
    if not isinstance(name, str) or not _VOLUME_NAME.fullmatch(name):
        raise VolumeContractError(
            "Volume name must match [A-Za-z0-9][A-Za-z0-9_.-]+"
        )
    return name


def validate_device_path(device: Path | str) -> Path:
    raw = os.fspath(device)
    path = Path(raw)
    if not path.is_absolute():
        raise VolumeContractError("Volume device must be an absolute path")
    if path == Path(path.anchor):
        raise VolumeContractError("Volume device cannot be the filesystem root")
    if ".." in path.parts:
        raise VolumeContractError("Volume device cannot contain '..'")

    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise VolumeContractError(f"Volume device traverses a symlink: {current}")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            raise VolumeContractError(f"Volume device parent is not a directory: {current}")
    return path


def prepare_device_directory(device: Path | str, *, create: bool = False) -> Path:
    """Validate a bind device and optionally create it without following symlinks."""
    path = validate_device_path(device)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                if not create:
                    raise VolumeContractError(f"Volume device does not exist: {path}") from None
                try:
                    os.mkdir(part, mode=0o750, dir_fd=descriptor)
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=descriptor,
                    )
                except OSError as exc:
                    raise VolumeContractError(
                        f"Could not safely create volume device component: {part}: {exc}"
                    ) from exc
            except OSError as exc:
                raise VolumeContractError(
                    f"Volume device component is not a real directory: {part}: {exc}"
                ) from exc
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    return path


def device_is_empty(device: Path | str) -> bool:
    path = prepare_device_directory(device)
    try:
        next(path.iterdir())
    except StopIteration:
        return True
    return False


def probe_device_filesystem(device: Path | str, *, require_empty: bool = False) -> dict[str, object]:
    """Exercise operations SQLite and the singleton lock require on a bind device."""
    path = prepare_device_directory(device)
    if require_empty and not device_is_empty(path):
        raise VolumeContractError(f"Volume device must be empty: {path}")

    probe_root = Path(tempfile.mkdtemp(prefix=".alphawave-volume-probe-", dir=path))
    source = probe_root / "source"
    destination = probe_root / "destination"
    try:
        descriptor = os.open(source, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        try:
            os.write(descriptor, b"alphawave-volume-probe")
            os.fsync(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
        os.replace(source, destination)
        if destination.read_bytes() != b"alphawave-volume-probe":
            raise VolumeContractError("Volume device failed atomic rename verification")
        filesystem = os.statvfs(path)
        return {
            "write": True,
            "flock": True,
            "atomic_rename": True,
            "available_bytes": filesystem.f_bavail * filesystem.f_frsize,
        }
    except (OSError, VolumeContractError) as exc:
        raise VolumeContractError(f"Volume device filesystem probe failed: {path}: {exc}") from exc
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)


def _single_inspection(payload: object) -> Mapping[str, Any]:
    if isinstance(payload, list):
        if len(payload) != 1 or not isinstance(payload[0], Mapping):
            raise VolumeContractError("Docker inspect must contain exactly one volume")
        return payload[0]
    if isinstance(payload, Mapping):
        return payload
    raise VolumeContractError("Docker inspect response is not an object")


def verify_volume_inspection(spec: VolumeSpec, payload: object) -> Mapping[str, Any]:
    """Fail closed unless Docker reports the exact requested volume contract."""
    inspection = _single_inspection(payload)
    if inspection.get("Name") != spec.name:
        raise VolumeContractError("Docker volume name does not match the requested volume")
    if inspection.get("Driver") != "local":
        raise VolumeContractError("Docker volume driver must be 'local'")
    raw_options = inspection.get("Options")
    if raw_options is None:
        options: dict[str, str] = {}
    elif isinstance(raw_options, Mapping) and all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw_options.items()
    ):
        options = dict(raw_options)
    else:
        raise VolumeContractError("Docker volume options are malformed")
    if options != spec.expected_options:
        raise VolumeContractError(
            f"Docker volume options differ from the requested contract: {options!r}"
        )
    return inspection


def verify_distinct_devices(first: Path | str, second: Path | str) -> tuple[Path, Path]:
    first_path = validate_device_path(first).resolve(strict=False)
    second_path = validate_device_path(second).resolve(strict=False)
    if first_path == second_path:
        raise VolumeContractError("Data and backup volume devices must be distinct")
    if first_path.exists() and second_path.exists() and os.path.samefile(first_path, second_path):
        raise VolumeContractError("Data and backup volume devices resolve to the same directory")
    return first_path, second_path


def parse_volume_inspection(spec: VolumeSpec, serialized: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise VolumeContractError("Docker inspect returned invalid JSON") from exc
    return verify_volume_inspection(spec, payload)


def _volume_spec_from_args(args: argparse.Namespace) -> VolumeSpec:
    device = Path(args.device) if args.device else None
    return VolumeSpec(args.name, args.mode, device)


def volume_device_check(args: argparse.Namespace) -> None:
    spec = _volume_spec_from_args(args)
    if spec.mode != EXTERNAL_BIND_BACKED:
        raise SystemExit("volume-device-check requires external-bind-backed mode")
    if args.validate_only:
        print("ok")
        return
    prepare_device_directory(spec.device, create=args.create)
    result = probe_device_filesystem(spec.device, require_empty=args.require_empty)
    print(json.dumps(result, sort_keys=True))


def volume_inspect_check(args: argparse.Namespace) -> None:
    spec = _volume_spec_from_args(args)
    serialized = (
        Path(args.input).read_text(encoding="utf-8")
        if args.input != "-"
        else sys.stdin.read()
    )
    parse_volume_inspection(spec, serialized)
    print("ok")


def volume_pair_check(args: argparse.Namespace) -> None:
    verify_distinct_devices(args.data_device, args.backup_device)
    print("ok")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as incoming:
            with sqlite3.connect(temporary) as outgoing:
                incoming.backup(outgoing)
                result = outgoing.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RuntimeError("SQLite integrity check failed")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def inspect_sqlite(path: Path) -> dict[str, object]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
        valid = bool(result and result[0] == "ok")
    except sqlite3.Error:
        valid = False
    return {
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
        "sqlite_integrity": "ok" if valid else "failed",
        "modified_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
    }


def manifest(args: argparse.Namespace) -> None:
    snapshot = Path(args.snapshot)
    counts = json.loads(_summary_json(snapshot))
    payload = {
        "format_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_runtime": "systemd",
        "service_name": args.service,
        "service_active": args.service_active == "active",
        "git_revision": args.git_revision or None,
        "data_volume": args.data_volume,
        "backup_volume": args.backup_volume,
        "volumes": {
            "mode": args.volume_mode,
            "data": {"name": args.data_volume, "device": args.data_volume_device or None},
            "backups": {
                "name": args.backup_volume,
                "device": args.backup_volume_device or None,
            },
        },
        "compose_config_sha256": args.compose_config_sha256 or None,
        "images": {"api": args.api_image_id or None, "web": args.web_image_id or None},
        "counts": counts,
        "database": inspect_sqlite(snapshot),
    }
    destination = Path(args.output)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def newest(args: argparse.Namespace) -> None:
    root = Path(args.directory)
    database = root / args.database
    candidates: list[tuple[int, Path, str]] = []
    if database.is_file() and inspect_sqlite(database)["sqlite_integrity"] == "ok":
        candidates.append((2**63 - 1, database, "database"))
    else:
        for backup in (root / args.backups).glob("*.sqlite.gz"):
            candidates.append((backup.stat().st_mtime_ns, backup, "backup"))
    if not candidates:
        raise SystemExit("No Docker database or backup artifacts found")
    _, selected, kind = max(candidates, key=lambda item: item[0])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        if kind == "backup":
            with gzip.open(selected, "rb") as source, temporary.open("wb") as destination:
                shutil.copyfileobj(source, destination)
        else:
            sqlite_snapshot(selected, temporary)
        if inspect_sqlite(temporary)["sqlite_integrity"] != "ok":
            raise RuntimeError("Newest Docker data artifact is not a valid SQLite database")
        os.replace(temporary, output)
        print(kind)
    finally:
        temporary.unlink(missing_ok=True)


def compose_override(args: argparse.Namespace) -> None:
    # JSON is valid YAML, which avoids quoting user-provided volume names by hand.
    payload = {
        "volumes": {
            "alphawave_data": {"external": True, "name": args.data_volume},
            "alphawave_backups": {"external": True, "name": args.backup_volume},
        }
    }
    Path(args.output).write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _summary_json(database: Path) -> str:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("SQLite integrity check failed")
        names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        counts = {
            name: connection.execute(
                f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"'
            ).fetchone()[0]
            for name in names
        }
    return json.dumps(counts, sort_keys=True)


def summary(args: argparse.Namespace) -> None:
    print(_summary_json(Path(args.database)))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("source")
    snapshot.add_argument("destination")
    make_manifest = commands.add_parser("manifest")
    make_manifest.add_argument("--snapshot", required=True)
    make_manifest.add_argument("--output", required=True)
    make_manifest.add_argument("--service", required=True)
    make_manifest.add_argument("--service-active", required=True)
    make_manifest.add_argument("--git-revision", default="")
    make_manifest.add_argument("--data-volume", required=True)
    make_manifest.add_argument("--backup-volume", required=True)
    make_manifest.add_argument("--volume-mode", choices=sorted(VOLUME_MODES), default=LOCAL_DRIVER_MANAGED)
    make_manifest.add_argument("--data-volume-device", default="")
    make_manifest.add_argument("--backup-volume-device", default="")
    make_manifest.add_argument("--compose-config-sha256", default="")
    make_manifest.add_argument("--api-image-id", default="")
    make_manifest.add_argument("--web-image-id", default="")
    select = commands.add_parser("newest")
    select.add_argument("--directory", required=True)
    select.add_argument("--database", default="alphawave-taskd.sqlite")
    select.add_argument("--backups", default="backups")
    select.add_argument("--output", required=True)
    override = commands.add_parser("compose-override")
    override.add_argument("--output", required=True)
    override.add_argument("--data-volume", required=True)
    override.add_argument("--backup-volume", required=True)
    summarize = commands.add_parser("summary")
    summarize.add_argument("database")
    device_check = commands.add_parser("volume-device-check")
    device_check.add_argument("--name", required=True)
    device_check.add_argument("--mode", choices=sorted(VOLUME_MODES), required=True)
    device_check.add_argument("--device")
    device_check.add_argument("--create", action="store_true")
    device_check.add_argument("--require-empty", action="store_true")
    device_check.add_argument("--validate-only", action="store_true")
    inspect_check = commands.add_parser("volume-inspect-check")
    inspect_check.add_argument("--name", required=True)
    inspect_check.add_argument("--mode", choices=sorted(VOLUME_MODES), required=True)
    inspect_check.add_argument("--device")
    inspect_check.add_argument("--input", default="-")
    pair_check = commands.add_parser("volume-pair-check")
    pair_check.add_argument("--data-device", required=True)
    pair_check.add_argument("--backup-device", required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        sqlite_snapshot(Path(args.source), Path(args.destination))
    elif args.command == "manifest":
        manifest(args)
    elif args.command == "newest":
        newest(args)
    elif args.command == "compose-override":
        compose_override(args)
    elif args.command == "volume-device-check":
        volume_device_check(args)
    elif args.command == "volume-inspect-check":
        volume_inspect_check(args)
    elif args.command == "volume-pair-check":
        volume_pair_check(args)
    else:
        summary(args)


if __name__ == "__main__":
    main()

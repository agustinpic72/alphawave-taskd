#!/usr/bin/env python3
"""Ownership and durable replacement primitives for runtime rollback tooling."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from dataclasses import asdict, dataclass
from pathlib import Path


class MetadataError(RuntimeError):
    """Raised when filesystem metadata is unsafe or cannot be restored."""


class SQLiteAccessError(RuntimeError):
    """Raised when the requested service identity cannot use a SQLite file."""


@dataclass(frozen=True)
class FileMetadata:
    uid: int
    gid: int
    mode: int

    def __post_init__(self) -> None:
        if self.uid < 0 or self.gid < 0:
            raise ValueError("UID and GID must be non-negative")
        if self.mode < 0 or self.mode > 0o7777:
            raise ValueError("mode must contain permission bits only")


@dataclass(frozen=True)
class OwnershipSnapshot:
    file: FileMetadata
    parent: FileMetadata

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {"file": asdict(self.file), "parent": asdict(self.parent)}

    @classmethod
    def from_dict(cls, payload: object) -> OwnershipSnapshot:
        if not isinstance(payload, dict):
            raise ValueError("ownership metadata must be an object")
        try:
            file_payload = payload["file"]
            parent_payload = payload["parent"]
            if not isinstance(file_payload, dict) or not isinstance(parent_payload, dict):
                raise TypeError
            return cls(file=FileMetadata(**file_payload), parent=FileMetadata(**parent_payload))
        except (KeyError, TypeError) as exc:
            raise ValueError("ownership metadata is incomplete") from exc

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> OwnershipSnapshot:
        try:
            return cls.from_dict(json.loads(payload))
        except json.JSONDecodeError as exc:
            raise ValueError("ownership metadata is not valid JSON") from exc


def _metadata(path: Path, *, directory: bool) -> FileMetadata:
    try:
        details = path.lstat()
    except OSError as exc:
        raise MetadataError(f"cannot inspect {path}") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(details.st_mode) or not expected(details.st_mode):
        kind = "directory" if directory else "regular file"
        raise MetadataError(f"{path} must be a non-symlink {kind}")
    return FileMetadata(details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode))


def _open_checked(path: Path, *, directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MetadataError(f"cannot safely open {path}") from exc
    details = os.fstat(descriptor)
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(details.st_mode):
        os.close(descriptor)
        kind = "directory" if directory else "regular file"
        raise MetadataError(f"{path} must be a non-symlink {kind}")
    return descriptor


def _descriptor_metadata(descriptor: int) -> FileMetadata:
    details = os.fstat(descriptor)
    return FileMetadata(details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode))


def capture_metadata(path: Path | str) -> OwnershipSnapshot:
    """Capture file and immediate-parent ownership without assuming a host UID."""

    target = Path(path)
    return OwnershipSnapshot(
        file=_metadata(target, directory=False),
        parent=_metadata(target.parent, directory=True),
    )


def apply_metadata(path: Path | str, metadata: FileMetadata) -> None:
    """Apply owner, group, and mode to an existing non-symlink regular file."""

    target = Path(path)
    descriptor = _open_checked(target, directory=False)
    try:
        current = _descriptor_metadata(descriptor)
        if (current.uid, current.gid) != (metadata.uid, metadata.gid):
            os.fchown(descriptor, metadata.uid, metadata.gid)
        os.fchmod(descriptor, metadata.mode)
        if _descriptor_metadata(descriptor) != metadata:
            raise MetadataError(f"metadata verification failed for {target}")
    except OSError as exc:
        raise MetadataError(f"cannot apply metadata to {target}") from exc
    finally:
        os.close(descriptor)


def apply_parent_metadata(path: Path | str, metadata: FileMetadata) -> None:
    """Apply captured metadata to a file's immediate, non-symlink parent."""

    parent = Path(path).parent
    descriptor = _open_checked(parent, directory=True)
    try:
        current = _descriptor_metadata(descriptor)
        if (current.uid, current.gid) != (metadata.uid, metadata.gid):
            os.fchown(descriptor, metadata.uid, metadata.gid)
        os.fchmod(descriptor, metadata.mode)
        if _descriptor_metadata(descriptor) != metadata:
            raise MetadataError(f"metadata verification failed for {parent}")
    except OSError as exc:
        raise MetadataError(f"cannot apply metadata to {parent}") from exc
    finally:
        os.close(descriptor)


def _probe_sqlite(path: Path, writable: bool) -> None:
    mode = "rw" if writable else "ro"
    with sqlite3.connect(f"file:{path}?mode={mode}", uri=True, timeout=2) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError("SQLite quick_check failed")
        if writable:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE __alphawave_access_probe (value INTEGER)")
            connection.rollback()


def probe_sqlite_access(
    path: Path | str,
    *,
    uid: int | None = None,
    gid: int | None = None,
    writable: bool = True,
) -> None:
    """Probe SQLite access under the requested identity without persisting writes.

    A privileged caller drops UID/GID in an isolated child. An unprivileged caller
    may only probe as its current effective identity.
    """

    target = Path(path)
    _metadata(target, directory=False)
    requested_uid = os.geteuid() if uid is None else uid
    requested_gid = os.getegid() if gid is None else gid
    if requested_uid < 0 or requested_gid < 0:
        raise ValueError("UID and GID must be non-negative")
    if os.geteuid() != 0 and (requested_uid != os.geteuid() or requested_gid != os.getegid()):
        raise PermissionError("changing probe identity requires root")

    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            if os.geteuid() == 0:
                os.setgroups([])
                os.setgid(requested_gid)
                os.setuid(requested_uid)
            _probe_sqlite(target, writable)
        except BaseException as exc:
            message = f"{type(exc).__name__}: {exc}".encode("utf-8", errors="replace")[:4096]
            os.write(write_fd, message)
            os.close(write_fd)
            os._exit(1)
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    error = b""
    try:
        while chunk := os.read(read_fd, 4096):
            error += chunk
    finally:
        os.close(read_fd)
    _, status = os.waitpid(child, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        detail = error.decode("utf-8", errors="replace") or "probe child failed"
        raise SQLiteAccessError(f"SQLite access probe failed: {detail}")


def fsync_file(path: Path | str) -> None:
    target = Path(path)
    _metadata(target, directory=False)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def fsync_directory(path: Path | str) -> None:
    target = Path(path)
    _metadata(target, directory=True)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def prepare_sqlite_replacement(
    prepared: Path | str,
    metadata: OwnershipSnapshot,
    *,
    restore_parent: bool = False,
    writable: bool = True,
    probe_uid: int | None = None,
    probe_gid: int | None = None,
) -> None:
    """Apply captured metadata and validate SQLite as the service identity."""

    candidate = Path(prepared)
    if restore_parent:
        apply_parent_metadata(candidate, metadata.parent)
    apply_metadata(candidate, metadata.file)
    probe_sqlite_access(
        candidate,
        uid=metadata.file.uid if probe_uid is None else probe_uid,
        gid=metadata.file.gid if probe_gid is None else probe_gid,
        writable=writable,
    )
    fsync_file(candidate)


def atomic_replace(prepared: Path | str, destination: Path | str) -> None:
    """Durably replace a file with a prepared file from the same directory."""

    candidate = Path(prepared).absolute()
    target = Path(destination).absolute()
    if candidate.parent != target.parent:
        raise ValueError("prepared file and destination must share a directory")
    _metadata(candidate, directory=False)
    _metadata(candidate.parent, directory=True)
    if target.exists() or target.is_symlink():
        _metadata(target, directory=False)
    fsync_file(candidate)
    try:
        os.replace(candidate, target)
    except OSError as exc:
        raise MetadataError(f"cannot atomically replace {target}") from exc
    fsync_directory(target.parent)

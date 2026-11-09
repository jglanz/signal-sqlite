"""Snapshot discovery, protobuf framing, and staged extraction."""

import gzip
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from google.protobuf.message import DecodeError

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite._proto import Backups_pb2 as proto
from signal_sqlite._proto import LocalBackup_pb2 as local_proto
from signal_sqlite.attachments import AttachmentExporter
from signal_sqlite.crypto import (
    decrypt_authenticated_file,
    decrypt_backup_id,
    derive_backup_key,
    derive_message_keys,
    hash_file,
    open_regular_file,
)
from signal_sqlite.database import Database
from signal_sqlite.manifest import build_manifest, write_manifest

SNAPSHOT_FILENAMES = (c.MAIN_NAME, c.METADATA_NAME, c.FILES_NAME)


@dataclass(frozen=True)
class ExtractionResult:
    snapshot: Path
    output: Path
    manifest: dict[str, Any]

    @property
    def complete(self) -> bool:
        return self.manifest[c.JSON_KEY_COMPLETE]


def select_snapshot(input_path: Path) -> Path:
    path = input_path.expanduser().resolve(strict=True)
    if not path.is_dir():
        raise BackupError("Input path must be a backup directory.")
    if all((path / name).is_file() for name in SNAPSHOT_FILENAMES):
        return path
    snapshots = sorted(
        (
            child
            for child in path.iterdir()
            if child.is_dir() and re.fullmatch(c.SNAPSHOT_PATTERN, child.name)
        ),
        reverse=True,
    )
    if not snapshots:
        raise BackupError("No Signal local backup snapshot was found in the input directory.")
    snapshot = snapshots[0]
    if not all((snapshot / name).is_file() for name in SNAPSHOT_FILENAMES):
        raise BackupError("The newest backup snapshot is missing main, metadata, or files.")
    return snapshot


def iter_records(stream: BinaryIO) -> Iterator[bytes]:
    """Read bounded length-delimited protobuf records and reject malformed varints."""
    while True:
        length = 0
        for index in range(c.MAX_VARINT_BYTES):
            data = stream.read(1)
            if not data:
                if index == 0:
                    return
                raise BackupError("Truncated protobuf record length.")
            byte = data[0]
            if index == c.MAX_VARINT_BYTES - 1 and byte > 1:
                raise BackupError("Protobuf record length overflows a 64-bit varint.")
            length |= (byte & c.VARINT_VALUE_MASK) << (index * c.VARINT_SHIFT_BITS)
            if not byte & c.VARINT_CONTINUATION_MASK:
                break
        else:
            raise BackupError("Invalid protobuf record length.")
        if length > c.MAX_FRAME_SIZE_BYTES:
            raise BackupError("Protobuf record exceeds the maximum supported size.")
        record = stream.read(length)
        if len(record) != length:
            raise BackupError("Truncated protobuf record.")
        yield record


def read_media_list(snapshot: Path) -> tuple[set[str], str]:
    path = snapshot / c.FILES_NAME
    source_hash = hash_file(path)
    names: set[str] = set()
    with open_regular_file(path) as stream:
        for raw in iter_records(stream):
            item = local_proto.FilesFrame.FromString(raw)
            if item.WhichOneof(c.FIELD_ITEM) != c.FIELD_MEDIA_NAME:
                raise BackupError("Backup files list contains an unsupported record.")
            if re.fullmatch(c.MEDIA_NAME_PATTERN, item.mediaName) is None:
                raise BackupError("Backup files list contains an invalid media filename.")
            names.add(item.mediaName)
    return names, source_hash


def read_metadata(snapshot: Path) -> tuple[local_proto.Metadata, str]:
    with open_regular_file(snapshot / c.METADATA_NAME) as stream:
        raw = stream.read(c.MAX_FRAME_SIZE_BYTES + 1)
    if len(raw) > c.MAX_FRAME_SIZE_BYTES:
        raise BackupError("Backup metadata is too large.")
    metadata = local_proto.Metadata.FromString(raw)
    if metadata.version != c.SUPPORTED_METADATA_VERSION:
        raise BackupError(f"Unsupported local backup metadata version: {metadata.version}.")
    return metadata, hashlib.sha256(raw).hexdigest()


@contextmanager
def private_umask() -> Iterator[None]:
    previous = os.umask(c.PRIVATE_UMASK)
    try:
        yield
    finally:
        os.umask(previous)


def _prepare_output(output_path: Path, backup_root: Path) -> Path:
    requested = output_path.expanduser()
    if requested.is_symlink():
        raise BackupError("Output path must not be a symbolic link.")
    output = requested.resolve()
    if output.is_relative_to(backup_root):
        raise BackupError("Output must be outside the source backup directory.")
    if output.exists() and not output.is_dir():
        raise BackupError("Output path is occupied by a file.")
    os.makedirs(output, mode=c.DIRECTORY_MODE, exist_ok=True)
    if any(output.iterdir()):
        raise BackupError("Output directory must be empty; existing files will not be overwritten.")
    return output


def _import_main(
    snapshot: Path, recovery_key: str, metadata: local_proto.Metadata, database: Database
) -> tuple[proto.BackupInfo, str]:
    backup_key = derive_backup_key(recovery_key)
    backup_id = decrypt_backup_id(backup_key, metadata.backupId.iv, metadata.backupId.encryptedId)
    keys = derive_message_keys(backup_key, backup_id)
    with tempfile.SpooledTemporaryFile(
        max_size=c.SPOOL_MEMORY_LIMIT_BYTES, mode=c.READ_WRITE_BINARY
    ) as decrypted:
        main_hash = decrypt_authenticated_file(snapshot / c.MAIN_NAME, decrypted, keys)
        decrypted.seek(0)
        with gzip.GzipFile(fileobj=decrypted, mode=c.READ_BINARY) as plaintext:
            records = iter_records(plaintext)
            raw_header = next(records, None)
            if raw_header is None:
                raise BackupError("Backup contains no header.")
            header = proto.BackupInfo.FromString(raw_header)
            if header.version != c.SUPPORTED_BACKUP_VERSION:
                raise BackupError(f"Unsupported message backup version: {header.version}.")
            database.add_record(raw_header, header, c.HEADER_KIND)
            for raw in records:
                database.add_frame(raw, proto.Frame.FromString(raw))
    return header, main_hash


def _extract_to_staging(
    snapshot: Path, recovery_key: str, staging: Path, *, skip_attachments: bool
) -> dict[str, Any]:
    metadata, metadata_hash = read_metadata(snapshot)
    media_names, files_hash = read_media_list(snapshot)
    attachments = AttachmentExporter(snapshot.parent, staging, skip=skip_attachments)
    database = Database(staging / c.DATABASE_NAME, attachments)
    try:
        header, main_hash = _import_main(snapshot, recovery_key, metadata, database)
        source_hashes = {
            c.MAIN_NAME: main_hash,
            c.METADATA_NAME: metadata_hash,
            c.FILES_NAME: files_hash,
        }
        for name, expected_hash in source_hashes.items():
            if hash_file(snapshot / name) != expected_hash:
                raise BackupError("Source backup changed during extraction.")
        counts = database.summary()
        counts[c.COUNT_LISTED_MEDIA] = len(media_names)
        counts[c.COUNT_REFERENCED_MEDIA] = len(attachments.referenced_media)
        complete = not database.warnings
        warnings = list(database.warnings)
        if counts[c.COUNT_UNKNOWN_FRAMES]:
            warnings.append("Unknown frame types were preserved as raw records; see frames table.")
        unreferenced = media_names - attachments.referenced_media
        if unreferenced:
            warnings.append(
                f"{len(unreferenced)} listed media files have no known FilePointer; "
                "their references remain in the source files list."
            )
        unlisted = attachments.referenced_media - media_names
        if unlisted:
            warnings.append(
                f"{len(unlisted)} attachment references are absent from the files list."
            )
            complete = False
        manifest = build_manifest(
            snapshot,
            header,
            source_hashes,
            counts,
            warnings,
            skip_attachments=skip_attachments,
            complete=complete,
        )
        database.finalize(manifest)
        write_manifest(staging / c.MANIFEST_NAME, manifest)
        return manifest
    finally:
        database.close()


def extract_backup(
    input_path: Path, output_path: Path, recovery_key: str, *, skip_attachments: bool = False
) -> ExtractionResult:
    snapshot = select_snapshot(input_path)
    staging: Path | None = None
    with private_umask():
        output = _prepare_output(output_path, snapshot.parent)
        try:
            staging = Path(tempfile.mkdtemp(prefix=c.STAGING_PREFIX, dir=output.parent))
            manifest = _extract_to_staging(
                snapshot, recovery_key, staging, skip_attachments=skip_attachments
            )
            if output.is_symlink() or any(output.iterdir()):
                raise BackupError(
                    "Output directory changed during extraction; nothing was replaced."
                )
            os.replace(staging, output)
            staging = None
        except DecodeError, gzip.BadGzipFile, EOFError, zlib.error:
            raise BackupError("Backup contains invalid protobuf or compressed data.") from None
        except sqlite3.Error:
            raise BackupError(
                "SQLite import failed; check backup references and FTS5 support."
            ) from None
        finally:
            if staging is not None:
                shutil.rmtree(staging)
    return ExtractionResult(snapshot=snapshot, output=output, manifest=manifest)

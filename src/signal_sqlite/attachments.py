"""Locate, authenticate, and export local media without trusting original filenames."""

import hashlib
import hmac
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite._proto import Backups_pb2 as proto
from signal_sqlite.crypto import attachment_keys, decrypt_authenticated_file


@dataclass(frozen=True)
class AttachmentResult:
    status: str
    media_name: str | None = None
    relative_path: str | None = None
    plaintext_sha256: str | None = None
    ciphertext_sha256: str | None = None
    error: str | None = None


def media_name_for_pointer(pointer: proto.FilePointer) -> str | None:
    locator = pointer.locatorInfo
    if len(locator.plaintextHash) != c.PLAINTEXT_HASH_LENGTH or not locator.localKey:
        return None
    return hashlib.sha256(locator.plaintextHash + locator.localKey).hexdigest()


def safe_suffix(pointer: proto.FilePointer) -> str:
    suffix = Path(pointer.fileName).suffix
    if re.fullmatch(c.SAFE_SUFFIX_PATTERN, suffix):
        return suffix.lower()
    guessed = mimetypes.guess_extension(pointer.contentType) or ""
    return guessed if re.fullmatch(c.SAFE_SUFFIX_PATTERN, guessed) else ""


class AttachmentExporter:
    def __init__(self, backup_root: Path, staging: Path, *, skip: bool) -> None:
        self.backup_root = backup_root
        self.staging = staging
        self.skip = skip
        self.referenced_media: set[str] = set()
        self._cache: dict[str, tuple[int, AttachmentResult]] = {}
        if not skip:
            (staging / c.ATTACHMENTS_NAME).mkdir(mode=c.DIRECTORY_MODE)

    def export(self, pointer: proto.FilePointer) -> AttachmentResult:
        media_name = media_name_for_pointer(pointer)
        if media_name is not None:
            self.referenced_media.add(media_name)
        if self.skip:
            return AttachmentResult(c.STATUS_SKIPPED, media_name=media_name)
        if not pointer.locatorInfo.localKey:
            return AttachmentResult(
                c.STATUS_UNAVAILABLE,
                media_name=media_name,
                error="Attachment was not stored locally in this backup.",
            )
        if media_name is None:
            return AttachmentResult(
                c.STATUS_FAILED, error="Local attachment is missing a valid plaintext hash."
            )
        if media_name in self._cache:
            cached_size, cached_result = self._cache[media_name]
            if cached_size != pointer.locatorInfo.size:
                return AttachmentResult(
                    c.STATUS_FAILED,
                    media_name=media_name,
                    error="Conflicting plaintext sizes for shared local media.",
                )
            return cached_result
        source = self.backup_root / c.FILES_NAME / media_name[: c.MEDIA_PREFIX_LENGTH] / media_name
        temporary: Path | None = None
        try:
            keys = attachment_keys(pointer.locatorInfo.localKey)
            directory = self.staging / c.ATTACHMENTS_NAME
            descriptor, name = tempfile.mkstemp(
                prefix=media_name + c.PATH_SEPARATOR, suffix=c.PART_SUFFIX, dir=directory
            )
            temporary = Path(name)
            with os.fdopen(descriptor, c.READ_WRITE_BINARY) as sink:
                source_hash = decrypt_authenticated_file(
                    source, sink, keys, plaintext_size=pointer.locatorInfo.size
                )
                sink.flush()
                sink.seek(0)
                plaintext_hash = hashlib.file_digest(sink, c.SHA256_NAME).hexdigest()
            if not hmac.compare_digest(plaintext_hash, pointer.locatorInfo.plaintextHash.hex()):
                raise BackupError("Decrypted attachment hash does not match the backup.")
            relative = Path(c.ATTACHMENTS_NAME) / (media_name + safe_suffix(pointer))
            temporary.replace(self.staging / relative)
            result = AttachmentResult(
                c.STATUS_EXPORTED,
                media_name=media_name,
                relative_path=relative.as_posix(),
                plaintext_sha256=plaintext_hash,
                ciphertext_sha256=source_hash,
            )
        except FileNotFoundError:
            result = AttachmentResult(
                c.STATUS_MISSING, media_name=media_name, error="Local attachment file is missing."
            )
        except BackupError as error:
            result = AttachmentResult(c.STATUS_FAILED, media_name=media_name, error=str(error))
        except OSError:
            result = AttachmentResult(
                c.STATUS_FAILED,
                media_name=media_name,
                error="Local attachment could not be read or written.",
            )
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self._cache[media_name] = pointer.locatorInfo.size, result
        return result

    def read_long_text(self, result: AttachmentResult) -> str | None:
        if result.relative_path is None:
            return None
        path = self.staging / result.relative_path
        if path.stat().st_size > c.MAX_LONG_TEXT_SIZE_BYTES:
            raise BackupError("Long-text attachment is too large to index safely.")
        try:
            return path.read_text(encoding=c.UTF8)
        except UnicodeError:
            raise BackupError("Long-text attachment is not valid UTF-8.") from None

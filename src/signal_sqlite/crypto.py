"""Signal key derivation and authenticated, bounded-memory AES decryption."""

import hashlib
import hmac
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from signal_sqlite import BackupError
from signal_sqlite import constants as c


@dataclass(frozen=True)
class KeyMaterial:
    aes_key: bytes
    hmac_key: bytes


def normalize_key(text: str) -> str:
    """Reverse Signal's display formatting: uppercase, whitespace, O → #, 0 → =.

    Reference: Signal Desktop's pinned formatBackupKeyForDisplay.std.ts.
    Reject other characters rather than guessing replacements.
    """
    stripped = "".join(text.split())
    key = stripped.lower().translate(c.KEY_DISPLAY_TRANSLATION)
    if not stripped.isascii() or re.fullmatch(c.KEY_PATTERN, key) is None:
        raise BackupError(
            "Recovery key must contain exactly 64 ASCII letters/digits; "
            "Signal's displayed # and = characters are accepted."
        )
    return key


@contextmanager
def open_regular_file(path: Path) -> Iterator[BinaryIO]:
    """Open one regular file without following a final symlink."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, c.READ_BINARY) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BackupError("Input must be a regular file.")
        yield stream


def read_key_file(path: Path) -> str:
    with open_regular_file(path) as stream:
        metadata = os.fstat(stream.fileno())
        if metadata.st_uid != os.getuid() or metadata.st_mode & c.OTHER_PERMISSION_BITS:
            raise BackupError("Key file must be owned by you with mode 0600 (chmod 600 PATH).")
        data = stream.read(c.MAX_KEY_FILE_SIZE_BYTES + 1)
    if len(data) > c.MAX_KEY_FILE_SIZE_BYTES:
        raise BackupError("Key file is too large.")
    try:
        return normalize_key(data.decode(c.KEY_TEXT_ENCODING))
    except UnicodeError:
        raise BackupError("Key file must contain UTF-8 text.") from None


def hkdf(input_key: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(input_key)


def derive_backup_key(recovery_key: str) -> bytes:
    return hkdf(normalize_key(recovery_key).encode(c.UTF8), c.BACKUP_KEY_INFO, c.BACKUP_KEY_LENGTH)


def decrypt_backup_id(backup_key: bytes, iv: bytes, encrypted_id: bytes) -> bytes:
    if len(iv) != c.METADATA_IV_LENGTH or len(encrypted_id) != c.BACKUP_ID_LENGTH:
        raise BackupError("Invalid backup metadata IV or encrypted ID length.")
    key = hkdf(backup_key, c.METADATA_KEY_INFO, c.AES_KEY_LENGTH)
    counter = iv + bytes(c.AES_IV_LENGTH - c.METADATA_IV_LENGTH)
    decryptor = Cipher(algorithms.AES(key), modes.CTR(counter)).decryptor()
    return decryptor.update(encrypted_id) + decryptor.finalize()


def derive_message_keys(backup_key: bytes, backup_id: bytes) -> KeyMaterial:
    material = hkdf(backup_key, c.MESSAGE_KEY_INFO + backup_id, c.COMBINED_KEY_LENGTH)
    return KeyMaterial(aes_key=material[c.HMAC_LENGTH :], hmac_key=material[: c.HMAC_LENGTH])


def attachment_keys(local_key: bytes) -> KeyMaterial:
    if len(local_key) != c.COMBINED_KEY_LENGTH:
        raise BackupError("Invalid local attachment key length.")
    return KeyMaterial(
        aes_key=local_key[: c.AES_KEY_LENGTH], hmac_key=local_key[c.AES_KEY_LENGTH :]
    )


def hash_file(path: Path) -> str:
    with open_regular_file(path) as stream:
        return hashlib.file_digest(stream, c.SHA256_NAME).hexdigest()


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise BackupError("Encrypted input was truncated or changed during extraction.")
    return data


def _authenticate(stream: BinaryIO, keys: KeyMaterial, body_length: int) -> tuple[bytes, str]:
    mac = hmac.new(keys.hmac_key, digestmod=c.SHA256_NAME)
    digest = hashlib.sha256()
    remaining = body_length
    while remaining:
        chunk = _read_exact(stream, min(remaining, c.BUFFER_SIZE_BYTES))
        mac.update(chunk)
        digest.update(chunk)
        remaining -= len(chunk)
    their_mac = _read_exact(stream, c.HMAC_LENGTH)
    digest.update(their_mac)
    if not hmac.compare_digest(mac.digest(), their_mac):
        raise BackupError("Authentication failed: the recovery key is wrong or data is corrupt.")
    return their_mac, digest.hexdigest()


def decrypt_authenticated_file(
    path: Path,
    sink: BinaryIO,
    keys: KeyMaterial,
    *,
    plaintext_size: int | None = None,
) -> str:
    """Authenticate before decryption; authenticate again against concurrent changes.

    The sink must be private staging storage and discarded by the caller on failure.
    Local attachments contain size-obscuring padding before the PKCS7 padding;
    plaintext_size removes that extra padding after authentication and decryption.
    """
    with open_regular_file(path) as stream:
        total_length = os.fstat(stream.fileno()).st_size
        body_length = total_length - c.HMAC_LENGTH
        encrypted_length = body_length - c.AES_IV_LENGTH
        if encrypted_length < c.AES_IV_LENGTH or encrypted_length % c.AES_IV_LENGTH:
            raise BackupError("Invalid encrypted input length.")
        their_mac, source_hash = _authenticate(stream, keys, body_length)
        stream.seek(0)
        iv = _read_exact(stream, c.AES_IV_LENGTH)
        mac = hmac.new(keys.hmac_key, iv, c.SHA256_NAME)
        decryptor = Cipher(algorithms.AES(keys.aes_key), modes.CBC(iv)).decryptor()
        unpadder = padding.PKCS7(c.AES_BLOCK_BITS).unpadder()
        remaining = encrypted_length
        written = 0

        def write_plaintext(data: bytes) -> None:
            nonlocal written
            if plaintext_size is not None:
                data = data[: max(0, plaintext_size - written)]
            sink.write(data)
            written += len(data)

        try:
            while remaining:
                chunk = _read_exact(stream, min(remaining, c.BUFFER_SIZE_BYTES))
                mac.update(chunk)
                write_plaintext(unpadder.update(decryptor.update(chunk)))
                remaining -= len(chunk)
            write_plaintext(unpadder.update(decryptor.finalize()) + unpadder.finalize())
        except ValueError:
            raise BackupError("Invalid encrypted input padding.") from None
        final_mac = _read_exact(stream, c.HMAC_LENGTH)
        if (
            not hmac.compare_digest(mac.digest(), their_mac)
            or not hmac.compare_digest(final_mac, their_mac)
            or stream.read(1)
        ):
            raise BackupError("Encrypted input changed during extraction.")
        if plaintext_size is not None and written != plaintext_size:
            raise BackupError("Decrypted attachment is shorter than its declared size.")
        return source_hash

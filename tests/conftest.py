"""Synthetic backups only. Never place private Signal data in the test suite."""

import gzip
import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite._proto import Backups_pb2 as proto
from signal_sqlite._proto import LocalBackup_pb2 as local_proto
from signal_sqlite.cli import cli

TEST_KEY = "dtjs858asj6tv0jzsqrsmj0ubp335pisj98e9ssnss8myoc08drhtcktyawvx45l"
TEST_DISPLAY_KEY = "DTJS858ASJ6TV=JZSQRSMJ=UBP335PISJ98E9SSNSS8MY#C=8DRHTCKTYAWVX45L"
TEST_ROOT_NAME = "SignalBackups"
TEST_SNAPSHOT_NAME = "signal-backup-2026-11-06-18-24-39"
TEST_KEY_FILENAME = "recovery.key"
TEST_OUTPUT_NAME = "export"
TEST_BACKUP_ID = bytes.fromhex("8a624fbc45379043f39f1391cddc5fe8")
TEST_LOCAL_KEY = bytes(range(c.COMBINED_KEY_LENGTH))
TEST_IV = bytes(range(c.AES_IV_LENGTH))
TEST_METADATA_IV = TEST_IV[: c.METADATA_IV_LENGTH]
SELF_ID = 1
CONTACT_ID = 2
CHAT_ID = 10
TEST_TIMESTAMP_MS = 1_720_000_000_000
REVISION_OFFSET_MS = 100
TEST_GZIP_PADDING_BYTES = 2048
TEST_ATTACHMENT_PADDING_BYTES = 777
TEST_ATTACHMENT_NAME = "../../report.txt"
TEST_CONTENT_TYPE = "text/plain"
TEST_TEXT = "Café résumé – project release"
TEST_QUOTE = "Earlier discussion"
TEST_REVISION = "Earlier draft"
TEST_CAPTION = "meeting handout"
TEST_ATTACHMENT_DATA = b"Quarterly employment notes.\n"
TEST_LONG_TEXT = "Full employment conversation beyond the truncated preview."
TEST_UNKNOWN_FRAME = b"\x9a\x06\x03xyz"
QUERY_BOB_ID = 3
QUERY_GROUP_ID = 4
QUERY_GROUP_CHAT_ID = 20
QUERY_EMPTY_CHAT_ID = 30
QUERY_GROUP_NAME = "Launch team"
QUERY_ALICE_NAME = "Ally"
QUERY_BOB_NAME = "Bob Builder"
QUERY_ALICE_ACI = bytes.fromhex("11111111111141118111111111111111")
QUERY_BOB_ACI = bytes.fromhex("22222222222242228222222222222222")
QUERY_UNKNOWN_ACI = bytes.fromhex("33333333333343338333333333333333")
QUERY_OWN_TEXT = "I wrote this message myself."
QUERY_GROUP_TEXT = "Group-only announcement."
QUERY_REMOTE_NAME = "remote-photo.png"
QUERY_REMOTE_TYPE = "image/png"
QUERY_REMOTE_SIZE = 100
QUERY_OWN_OFFSET_MS = 1000
QUERY_RECEIVED_OFFSET_MS = 1500
QUERY_GROUP_OFFSET_MS = 2000
QUERY_CHANGE_OFFSET_MS = 3000


def encode_record(raw: bytes) -> bytes:
    length = len(raw)
    result = bytearray()
    while length >= c.VARINT_CONTINUATION_MASK:
        result.append((length & c.VARINT_VALUE_MASK) | c.VARINT_CONTINUATION_MASK)
        length >>= c.VARINT_SHIFT_BITS
    result.append(length)
    return bytes(result) + raw


def fixture_hkdf(key: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(key)


def encrypt_file(plaintext: bytes, aes_key: bytes, mac_key: bytes) -> bytes:
    padder = padding.PKCS7(c.AES_BLOCK_BITS).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(TEST_IV)).encryptor()
    body = TEST_IV + cipher.update(padded) + cipher.finalize()
    return body + hmac.digest(mac_key, body, c.SHA256_NAME)


@dataclass
class SyntheticBackup:
    root: Path
    snapshot: Path
    key_path: Path
    output: Path
    attachment_source: Path
    attachment_data: bytes
    raw_records: list[bytes]
    header: proto.BackupInfo
    frames: list[proto.Frame]

    def rewrite(self, *, raw_plaintext: bytes | None = None) -> None:
        records = [
            self.header.SerializeToString(),
            *(frame.SerializeToString() for frame in self.frames),
        ]
        records.append(TEST_UNKNOWN_FRAME)
        self.raw_records = records
        plaintext = (
            b"".join(encode_record(raw) for raw in records)
            if raw_plaintext is None
            else raw_plaintext
        )
        backup_key = fixture_hkdf(TEST_KEY.encode(c.UTF8), c.BACKUP_KEY_INFO, c.BACKUP_KEY_LENGTH)
        material = fixture_hkdf(
            backup_key, c.MESSAGE_KEY_INFO + TEST_BACKUP_ID, c.COMBINED_KEY_LENGTH
        )
        compressed = gzip.compress(plaintext) + bytes(TEST_GZIP_PADDING_BYTES)
        (self.snapshot / c.MAIN_NAME).write_bytes(
            encrypt_file(compressed, material[c.HMAC_LENGTH :], material[: c.HMAC_LENGTH])
        )


def _write_attachment(root: Path, data: bytes) -> tuple[proto.FilePointer, Path]:
    plaintext_hash = hashlib.sha256(data).digest()
    name = hashlib.sha256(plaintext_hash + TEST_LOCAL_KEY).hexdigest()
    source = root / c.FILES_NAME / name[: c.MEDIA_PREFIX_LENGTH] / name
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(
        encrypt_file(
            data + bytes(TEST_ATTACHMENT_PADDING_BYTES),
            TEST_LOCAL_KEY[: c.AES_KEY_LENGTH],
            TEST_LOCAL_KEY[c.AES_KEY_LENGTH :],
        )
    )
    pointer = proto.FilePointer(
        contentType=TEST_CONTENT_TYPE,
        fileName=TEST_ATTACHMENT_NAME,
        caption=TEST_CAPTION,
        locatorInfo=proto.FilePointer.LocatorInfo(
            key=TEST_LOCAL_KEY,
            localKey=TEST_LOCAL_KEY,
            plaintextHash=plaintext_hash,
            size=len(data),
        ),
    )
    return pointer, source


def _frames(pointer: proto.FilePointer, *, long_text: bool) -> list[proto.Frame]:
    standard = proto.StandardMessage(
        text=proto.Text(body=TEST_TEXT),
        quote=proto.Quote(authorId=SELF_ID, text=proto.Text(body=TEST_QUOTE)),
        reactions=[proto.Reaction(emoji="👍", authorId=SELF_ID, sentTimestamp=TEST_TIMESTAMP_MS)],
    )
    if long_text:
        standard.longText.CopyFrom(pointer)
    else:
        standard.attachments.add(pointer=pointer)
    item = proto.ChatItem(
        chatId=CHAT_ID,
        authorId=CONTACT_ID,
        dateSent=TEST_TIMESTAMP_MS,
        incoming=proto.ChatItem.IncomingMessageDetails(dateReceived=TEST_TIMESTAMP_MS),
        standardMessage=standard,
        revisions=[
            proto.ChatItem(
                chatId=CHAT_ID,
                authorId=CONTACT_ID,
                dateSent=TEST_TIMESTAMP_MS - REVISION_OFFSET_MS,
                incoming=proto.ChatItem.IncomingMessageDetails(dateReceived=TEST_TIMESTAMP_MS),
                standardMessage=proto.StandardMessage(text=proto.Text(body=TEST_REVISION)),
            )
        ],
    )
    return [
        proto.Frame(account=proto.AccountData(givenName="Local User")),
        proto.Frame(recipient=proto.Recipient(id=SELF_ID, self=proto.Self())),
        proto.Frame(
            recipient=proto.Recipient(
                id=CONTACT_ID,
                contact=proto.Contact(profileGivenName="Alice", profileFamilyName="Example"),
            )
        ),
        proto.Frame(chat=proto.Chat(id=CHAT_ID, recipientId=CONTACT_ID)),
        proto.Frame(chatItem=item),
    ]


@pytest.fixture
def backup_factory(tmp_path: Path) -> Callable[..., SyntheticBackup]:
    def make(*, long_text: bool = False, empty_attachment: bool = False) -> SyntheticBackup:
        root = tmp_path / TEST_ROOT_NAME
        snapshot = root / TEST_SNAPSHOT_NAME
        snapshot.mkdir(parents=True)
        key_path = tmp_path / TEST_KEY_FILENAME
        key_path.write_text(TEST_KEY, encoding=c.UTF8)
        key_path.chmod(c.FILE_MODE)
        backup_key = fixture_hkdf(TEST_KEY.encode(c.UTF8), c.BACKUP_KEY_INFO, c.BACKUP_KEY_LENGTH)
        metadata_key = fixture_hkdf(backup_key, c.METADATA_KEY_INFO, c.AES_KEY_LENGTH)
        counter = TEST_METADATA_IV + bytes(c.AES_IV_LENGTH - c.METADATA_IV_LENGTH)
        cipher = Cipher(algorithms.AES(metadata_key), modes.CTR(counter)).encryptor()
        metadata = local_proto.Metadata(
            version=c.SUPPORTED_METADATA_VERSION,
            backupId=local_proto.Metadata.EncryptedBackupId(
                iv=TEST_METADATA_IV, encryptedId=cipher.update(TEST_BACKUP_ID) + cipher.finalize()
            ),
        )
        (snapshot / c.METADATA_NAME).write_bytes(metadata.SerializeToString())
        data = TEST_LONG_TEXT.encode(c.UTF8) if long_text else TEST_ATTACHMENT_DATA
        if empty_attachment:
            data = b""
        pointer, source = _write_attachment(root, data)
        (snapshot / c.FILES_NAME).write_bytes(
            encode_record(local_proto.FilesFrame(mediaName=source.name).SerializeToString())
        )
        result = SyntheticBackup(
            root=root,
            snapshot=snapshot,
            key_path=key_path,
            output=tmp_path / TEST_OUTPUT_NAME,
            attachment_source=source,
            attachment_data=data,
            raw_records=[],
            header=proto.BackupInfo(
                version=c.SUPPORTED_BACKUP_VERSION,
                backupTimeMs=TEST_TIMESTAMP_MS,
                currentAppVersion="synthetic-test",
            ),
            frames=_frames(pointer, long_text=long_text),
        )
        result.rewrite()
        return result

    return make


@pytest.fixture
def backup(backup_factory: Callable[..., SyntheticBackup]) -> SyntheticBackup:
    return backup_factory()


@pytest.fixture
def invoke_extract() -> Callable[..., Result]:
    def invoke(backup: SyntheticBackup, *options: str, output: Path | None = None) -> Result:
        return CliRunner().invoke(
            cli,
            [
                c.COMMAND_EXTRACT_BACKUP,
                c.OPTION_KEY_FILE,
                str(backup.key_path),
                *options,
                str(backup.root),
                str(output if output is not None else backup.output),
            ],
        )

    return invoke


@pytest.fixture
def query_database() -> Callable:
    def query(backup: SyntheticBackup, sql: str, parameters: tuple = ()) -> list[tuple]:
        with closing(sqlite3.connect(backup.output / c.DATABASE_NAME)) as connection:
            return connection.execute(sql, parameters).fetchall()

    return query


@pytest.fixture
def query_export(backup: SyntheticBackup, invoke_extract: Callable) -> SyntheticBackup:
    alice = backup.frames[2].recipient.contact
    alice.aci = QUERY_ALICE_ACI
    alice.nickname.given = QUERY_ALICE_NAME
    bob = proto.Frame(
        recipient=proto.Recipient(
            id=QUERY_BOB_ID,
            contact=proto.Contact(
                aci=QUERY_BOB_ACI, profileGivenName="Bob", profileFamilyName="Builder"
            ),
        )
    )
    group = proto.Frame(
        recipient=proto.Recipient(
            id=QUERY_GROUP_ID,
            group=proto.Group(
                snapshot=proto.Group.GroupSnapshot(
                    title=proto.Group.GroupAttributeBlob(title=QUERY_GROUP_NAME),
                    members=[
                        proto.Group.Member(userId=value)
                        for value in (
                            QUERY_ALICE_ACI,
                            QUERY_BOB_ACI,
                            QUERY_UNKNOWN_ACI,
                        )
                    ],
                )
            ),
        )
    )
    backup.frames[3:3] = [bob, group]
    own = proto.ChatItem(
        chatId=CHAT_ID,
        authorId=SELF_ID,
        dateSent=TEST_TIMESTAMP_MS + QUERY_OWN_OFFSET_MS,
        outgoing=proto.ChatItem.OutgoingMessageDetails(
            dateReceived=TEST_TIMESTAMP_MS + QUERY_RECEIVED_OFFSET_MS
        ),
        standardMessage=proto.StandardMessage(text=proto.Text(body=QUERY_OWN_TEXT)),
    )
    own.standardMessage.attachments.add().CopyFrom(
        backup.frames[-1].chatItem.standardMessage.attachments[0]
    )
    own.standardMessage.attachments.add(
        pointer=proto.FilePointer(
            fileName=QUERY_REMOTE_NAME,
            contentType=QUERY_REMOTE_TYPE,
            locatorInfo=proto.FilePointer.LocatorInfo(key=TEST_LOCAL_KEY, size=QUERY_REMOTE_SIZE),
        )
    )
    backup.frames.extend(
        [
            proto.Frame(chatItem=own),
            proto.Frame(chat=proto.Chat(id=QUERY_GROUP_CHAT_ID, recipientId=QUERY_GROUP_ID)),
            proto.Frame(chat=proto.Chat(id=QUERY_EMPTY_CHAT_ID, recipientId=QUERY_BOB_ID)),
            proto.Frame(
                chatItem=proto.ChatItem(
                    chatId=QUERY_GROUP_CHAT_ID,
                    authorId=QUERY_BOB_ID,
                    dateSent=TEST_TIMESTAMP_MS + QUERY_GROUP_OFFSET_MS,
                    incoming=proto.ChatItem.IncomingMessageDetails(
                        dateReceived=TEST_TIMESTAMP_MS + QUERY_GROUP_OFFSET_MS
                    ),
                    standardMessage=proto.StandardMessage(text=proto.Text(body=QUERY_GROUP_TEXT)),
                )
            ),
            proto.Frame(
                chatItem=proto.ChatItem(
                    chatId=QUERY_GROUP_CHAT_ID,
                    dateSent=TEST_TIMESTAMP_MS + QUERY_CHANGE_OFFSET_MS,
                    directionless=proto.ChatItem.DirectionlessMessageDetails(),
                    updateMessage=proto.ChatUpdateMessage(),
                )
            ),
        ]
    )
    backup.rewrite()
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    return backup


@pytest.fixture
def invoke_query() -> Callable:
    def invoke(
        export: SyntheticBackup,
        *arguments: str,
        output_format: str = q.FORMAT_JSON,
        database: Path | None = None,
        terminal_width: int | None = None,
    ) -> Result:
        return CliRunner().invoke(
            cli,
            [
                q.OPTION_FORMAT,
                output_format,
                q.OPTION_DATABASE,
                str(database if database is not None else export.output / c.DATABASE_NAME),
                *arguments,
            ],
            terminal_width=terminal_width,
        )

    return invoke


@pytest.fixture
def query_payload(invoke_query: Callable) -> Callable:
    def payload(export: SyntheticBackup, *arguments: str) -> dict:
        result = invoke_query(export, *arguments)
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout)

    return payload

"""Lossless record storage, relational projections, and SQLite full-text search."""

import json
import sqlite3
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

from google.protobuf.json_format import MessageToJson
from google.protobuf.message import Message

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite._proto import Backups_pb2 as proto
from signal_sqlite.attachments import AttachmentExporter

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE export_metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
CREATE TABLE frames (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
    raw_protobuf BLOB NOT NULL, decoded_json TEXT NOT NULL
);
CREATE TABLE recipients (
    id TEXT PRIMARY KEY, frame_id INTEGER NOT NULL REFERENCES frames(id),
    kind TEXT NOT NULL, display_name TEXT, phone_number TEXT, aci TEXT, pni TEXT, username TEXT
);
CREATE TABLE chats (
    id TEXT PRIMARY KEY, frame_id INTEGER NOT NULL REFERENCES frames(id),
    recipient_id TEXT NOT NULL REFERENCES recipients(id), archived INTEGER NOT NULL
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL REFERENCES frames(id),
    parent_message_id INTEGER REFERENCES messages(id), revision_index INTEGER,
    chat_id TEXT NOT NULL REFERENCES chats(id), author_id TEXT REFERENCES recipients(id),
    date_sent_ms INTEGER NOT NULL, date_received_ms INTEGER,
    direction TEXT NOT NULL, kind TEXT NOT NULL,
    body TEXT NOT NULL, quoted_text TEXT NOT NULL,
    other_text TEXT NOT NULL, file_names TEXT NOT NULL, decoded_json TEXT NOT NULL
);
CREATE INDEX messages_chat_date ON messages(chat_id, date_sent_ms);
CREATE INDEX messages_author ON messages(author_id);
CREATE INDEX messages_parent ON messages(parent_message_id);
CREATE TABLE reactions (
    id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES messages(id),
    author_id TEXT NOT NULL REFERENCES recipients(id), emoji TEXT NOT NULL,
    sent_timestamp_ms INTEGER NOT NULL, sort_order TEXT NOT NULL
);
CREATE TABLE attachments (
    id INTEGER PRIMARY KEY, frame_id INTEGER NOT NULL REFERENCES frames(id),
    message_id INTEGER REFERENCES messages(id), field_path TEXT NOT NULL,
    file_name TEXT, content_type TEXT, caption TEXT, size_bytes INTEGER NOT NULL,
    media_name TEXT, exported_path TEXT, plaintext_sha256 TEXT, ciphertext_sha256 TEXT,
    status TEXT NOT NULL, error TEXT
);
CREATE INDEX attachments_message ON attachments(message_id);
CREATE VIRTUAL TABLE messages_fts USING fts5(
    body, quoted_text, other_text, file_names,
    content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE VIEW message_details AS
SELECT m.*, author.display_name AS author_name,
       peer.display_name AS conversation_name,
       datetime(m.date_sent_ms / 1000.0, 'unixepoch') AS date_sent_utc
FROM messages m
LEFT JOIN recipients author ON author.id = m.author_id
JOIN chats chat ON chat.id = m.chat_id
JOIN recipients peer ON peer.id = chat.recipient_id;
"""
INSERT_FRAME_SQL = "INSERT INTO frames VALUES (?, ?, ?, ?)"
INSERT_RECIPIENT_SQL = "INSERT INTO recipients VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
INSERT_CHAT_SQL = "INSERT INTO chats VALUES (?, ?, ?, ?)"
INSERT_MESSAGE_SQL = """
INSERT INTO messages (
    frame_id, parent_message_id, revision_index, chat_id, author_id,
    date_sent_ms, date_received_ms, direction, kind, body, quoted_text,
    other_text, file_names, decoded_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
INSERT_REACTION_SQL = """
INSERT INTO reactions (message_id, author_id, emoji, sent_timestamp_ms, sort_order)
VALUES (?, ?, ?, ?, ?)
"""
INSERT_ATTACHMENT_SQL = """
INSERT INTO attachments (
    frame_id, message_id, field_path, file_name, content_type, caption, size_bytes,
    media_name, exported_path, plaintext_sha256, ciphertext_sha256, status, error
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
UPDATE_MESSAGE_TEXT_SQL = "UPDATE messages SET body = ?, other_text = ? WHERE id = ?"
INSERT_METADATA_SQL = "INSERT INTO export_metadata VALUES (?, ?)"
REBUILD_FTS_SQL = "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')"
CHECK_FTS_SQL = "INSERT INTO messages_fts(messages_fts, rank) VALUES ('integrity-check', 1)"
CHECK_DATABASE_SQL = "PRAGMA integrity_check"
CHECK_FOREIGN_KEYS_SQL = "PRAGMA foreign_key_check"
SCHEMA_VERSION_SQL = f"PRAGMA user_version = {c.DATABASE_SCHEMA_VERSION}"
INTEGRITY_OK = "ok"
REPEATED_FIELD_PATH = "{name}[{index}]"
DISPLAY_SELF = "Note to Self"
DISPLAY_RELEASE_NOTES = "Signal"
KIND_RELEASE_NOTES = "releaseNotes"
KIND_CALL_LINK = "callLink"
KIND_DISTRIBUTION_LIST = "distributionList"


def decoded_json(message: Message) -> str:
    return MessageToJson(
        message, preserving_proto_field_name=True, indent=None, sort_keys=True, ensure_ascii=False
    )


def walk_messages(
    message: Message, path: str = "", depth: int = 0
) -> Iterator[tuple[str, Message]]:
    if depth > c.MAX_PROTO_DEPTH:
        raise BackupError("Backup message nesting is too deep.")
    yield path, message
    for field, value in message.ListFields():
        if field.message_type is None or field.name == c.FIELD_REVISIONS:
            continue
        children = enumerate(value) if field.is_repeated else [(None, value)]
        for index, child in children:
            name = (
                REPEATED_FIELD_PATH.format(name=field.name, index=index)
                if index is not None
                else field.name
            )
            child_path = c.PATH_SEPARATOR.join(filter(None, (path, name)))
            yield from walk_messages(child, child_path, depth + 1)


def text_values(message: Message) -> Iterator[str]:
    for _, node in walk_messages(message):
        for field, value in node.ListFields():
            if field.type == c.STRING_FIELD_TYPE:
                yield from value if field.is_repeated else [value]


def sqlite_integer(value: int) -> int:
    if value > c.MAX_SQLITE_INTEGER:
        raise BackupError("A backup timestamp exceeds SQLite's integer range.")
    return value


def _uuid_or_hex(value: bytes) -> str | None:
    if not value:
        return None
    return str(UUID(bytes=value)) if len(value) == c.BACKUP_ID_LENGTH else value.hex()


def _person_name(given: str, family: str) -> str:
    return c.NAME_SEPARATOR.join(filter(None, (given, family)))


def _recipient_name(recipient: proto.Recipient, self_name: str) -> str:
    kind = recipient.WhichOneof(c.FIELD_DESTINATION)
    if kind == c.KIND_CONTACT:
        contact = recipient.contact
        return (
            _person_name(contact.nickname.given, contact.nickname.family)
            or contact.systemNickname
            or _person_name(contact.systemGivenName, contact.systemFamilyName)
            or _person_name(contact.profileGivenName, contact.profileFamilyName)
            or _person_name(contact.sharedName.given, contact.sharedName.family)
            or contact.username
            or (str(contact.e164) if contact.e164 else "")
            or str(recipient.id)
        )
    if kind == c.KIND_GROUP:
        return recipient.group.snapshot.title.title or str(recipient.id)
    if kind == c.KIND_SELF:
        return self_name or DISPLAY_SELF
    if kind == KIND_RELEASE_NOTES:
        return DISPLAY_RELEASE_NOTES
    if kind == KIND_CALL_LINK:
        return recipient.callLink.name or str(recipient.id)
    if kind == KIND_DISTRIBUTION_LIST:
        return recipient.distributionList.distributionList.name or str(recipient.id)
    return str(recipient.id)


class Database:
    def __init__(self, path: Path, attachments: AttachmentExporter) -> None:
        path.touch(mode=c.FILE_MODE, exist_ok=False)
        self.connection = sqlite3.connect(path)
        self.attachments = attachments
        self.counts: Counter[str] = Counter()
        self.attachment_statuses: Counter[str] = Counter()
        self.warnings: list[str] = []
        self.self_name = ""
        self._frame_id = 0
        try:
            self.connection.executescript(SCHEMA_SQL)
            self.connection.execute(SCHEMA_VERSION_SQL)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def add_record(self, raw: bytes, message: Message, kind: str) -> int:
        frame_id = self._frame_id
        self._frame_id += 1
        self.connection.execute(INSERT_FRAME_SQL, (frame_id, kind, raw, decoded_json(message)))
        self.counts[c.COUNT_FRAMES] += 1
        if kind == c.UNKNOWN_KIND:
            self.counts[c.COUNT_UNKNOWN_FRAMES] += 1
        return frame_id

    def add_frame(self, raw: bytes, frame: proto.Frame) -> None:
        kind = frame.WhichOneof(c.FIELD_ITEM) or c.UNKNOWN_KIND
        frame_id = self.add_record(raw, frame, kind)
        if kind == c.KIND_ACCOUNT:
            self.self_name = _person_name(frame.account.givenName, frame.account.familyName)
        elif kind == c.KIND_RECIPIENT:
            recipient = frame.recipient
            recipient_kind = recipient.WhichOneof(c.FIELD_DESTINATION) or c.UNKNOWN_KIND
            contact = recipient.contact
            self.connection.execute(
                INSERT_RECIPIENT_SQL,
                (
                    str(recipient.id),
                    frame_id,
                    recipient_kind,
                    _recipient_name(recipient, self.self_name),
                    str(contact.e164) if contact.e164 else None,
                    _uuid_or_hex(contact.aci),
                    _uuid_or_hex(contact.pni),
                    contact.username or None,
                ),
            )
            self.counts[c.COUNT_RECIPIENTS] += 1
        elif kind == c.KIND_CHAT:
            chat = frame.chat
            self.connection.execute(
                INSERT_CHAT_SQL, (str(chat.id), frame_id, str(chat.recipientId), chat.archived)
            )
            self.counts[c.COUNT_CHATS] += 1
        elif kind == c.KIND_CHAT_ITEM:
            self._add_message(frame.chatItem, frame_id)
            return
        self._add_attachments(frame, frame_id, None)

    def _add_message(
        self,
        item: proto.ChatItem,
        frame_id: int,
        parent_id: int | None = None,
        revision_index: int | None = None,
        inherited_chat: int = 0,
        inherited_author: int = 0,
        depth: int = 0,
    ) -> None:
        if depth > c.MAX_PROTO_DEPTH:
            raise BackupError("Message revision nesting is too deep.")
        kind = item.WhichOneof(c.FIELD_ITEM) or c.UNKNOWN_KIND
        direction = item.WhichOneof(c.FIELD_DIRECTION) or c.UNKNOWN_KIND
        chat_id = item.chatId or inherited_chat
        author_id = item.authorId or inherited_author
        body = ""
        quote = ""
        if kind == c.KIND_STANDARD_MESSAGE:
            body = item.standardMessage.text.body
            quote = item.standardMessage.quote.text.body
        elif kind == c.KIND_STORY_REPLY:
            body = item.directStoryReplyMessage.textReply.text.body
        strings = list(text_values(item))
        other_text = c.TEXT_SEPARATOR.join(text for text in strings if text not in (body, quote))
        pointers = [
            node
            for _, node in walk_messages(item)
            if node.DESCRIPTOR is proto.FilePointer.DESCRIPTOR
        ]
        names = c.TEXT_SEPARATOR.join(pointer.fileName for pointer in pointers if pointer.fileName)
        received = None
        if direction == c.DIRECTION_INCOMING:
            received = item.incoming.dateReceived
        elif direction == c.DIRECTION_OUTGOING:
            received = item.outgoing.dateReceived
        cursor = self.connection.execute(
            INSERT_MESSAGE_SQL,
            (
                frame_id,
                parent_id,
                revision_index,
                str(chat_id),
                str(author_id) if author_id else None,
                sqlite_integer(item.dateSent),
                sqlite_integer(received) if received is not None else None,
                direction,
                kind,
                body,
                quote,
                other_text,
                names,
                decoded_json(item),
            ),
        )
        message_id = cursor.lastrowid
        count_key = c.COUNT_MESSAGES if parent_id is None else c.COUNT_REVISIONS
        self.counts[count_key] += 1
        long_texts = self._add_attachments(item, frame_id, message_id)
        if long_texts:
            full_body = c.TEXT_SEPARATOR.join(long_texts)
            # Keep an independently exported prefix searchable if it differs.
            previous_body = body if body and body != full_body else ""
            self.connection.execute(
                UPDATE_MESSAGE_TEXT_SQL,
                (
                    full_body,
                    c.TEXT_SEPARATOR.join(filter(None, (other_text, previous_body))),
                    message_id,
                ),
            )
        for _, node in walk_messages(item):
            if node.DESCRIPTOR is proto.Reaction.DESCRIPTOR:
                self.connection.execute(
                    INSERT_REACTION_SQL,
                    (
                        message_id,
                        str(node.authorId),
                        node.emoji,
                        sqlite_integer(node.sentTimestamp),
                        str(node.sortOrder),
                    ),
                )
        for index, revision in enumerate(item.revisions):
            self._add_message(revision, frame_id, message_id, index, chat_id, author_id, depth + 1)

    def _add_attachments(
        self, message: Message, frame_id: int, message_id: int | None
    ) -> list[str]:
        long_texts: list[str] = []
        for field_path, pointer in walk_messages(message):
            if pointer.DESCRIPTOR is not proto.FilePointer.DESCRIPTOR:
                continue
            result = self.attachments.export(pointer)
            error = result.error
            if field_path.endswith(c.LONG_TEXT_FIELD_NAME) and result.status == c.STATUS_EXPORTED:
                try:
                    text = self.attachments.read_long_text(result)
                    if text is not None:
                        long_texts.append(text)
                except BackupError as indexing_error:
                    error = str(indexing_error)
                    self.warnings.append(f"Frame {frame_id}: {error}")
            self.connection.execute(
                INSERT_ATTACHMENT_SQL,
                (
                    frame_id,
                    message_id,
                    field_path,
                    pointer.fileName or None,
                    pointer.contentType or None,
                    pointer.caption or None,
                    pointer.locatorInfo.size,
                    result.media_name,
                    result.relative_path,
                    result.plaintext_sha256,
                    result.ciphertext_sha256,
                    result.status,
                    error,
                ),
            )
            self.counts[c.COUNT_ATTACHMENTS] += 1
            self.attachment_statuses[result.status] += 1
            if result.status in c.ATTACHMENT_FAILURE_STATUSES:
                self.warnings.append(f"Frame {frame_id}, {field_path}: {error}")
        return long_texts

    def summary(self) -> dict[str, Any]:
        keys = (
            c.COUNT_FRAMES,
            c.COUNT_UNKNOWN_FRAMES,
            c.COUNT_RECIPIENTS,
            c.COUNT_CHATS,
            c.COUNT_MESSAGES,
            c.COUNT_REVISIONS,
            c.COUNT_ATTACHMENTS,
        )
        return {
            **{key: self.counts[key] for key in keys},
            c.COUNT_ATTACHMENT_STATUSES: dict(self.attachment_statuses),
        }

    def finalize(self, manifest: dict[str, Any]) -> None:
        self.connection.executemany(
            INSERT_METADATA_SQL,
            ((key, json.dumps(value, ensure_ascii=False)) for key, value in manifest.items()),
        )
        self.connection.execute(REBUILD_FTS_SQL)
        self.connection.execute(CHECK_FTS_SQL)
        if self.connection.execute(CHECK_DATABASE_SQL).fetchone()[0] != INTEGRITY_OK:
            raise BackupError("SQLite integrity verification failed.")
        if self.connection.execute(CHECK_FOREIGN_KEYS_SQL).fetchone() is not None:
            raise BackupError("Backup contains unresolved recipient, chat, or message references.")
        self.connection.commit()

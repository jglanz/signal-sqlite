"""Read-only channel and conversation queries over an existing extracted database."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from google.protobuf.message import DecodeError

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite._proto import Backups_pb2 as proto

QUERY_SCHEMA_VERSION = "PRAGMA user_version"
QUERY_ONLY_SQL = "PRAGMA query_only = ON"
BEGIN_READ_SQL = "BEGIN"
RECIPIENTS_SQL = """
SELECT r.*, f.raw_protobuf FROM recipients r JOIN frames f ON f.id = r.frame_id
"""
CHANNELS_SQL = """
SELECT ch.id, ch.recipient_id,
       MAX(CASE WHEN m.date_received_ms > m.date_sent_ms
                THEN m.date_received_ms ELSE m.date_sent_ms END) AS last_activity_ms
FROM chats ch LEFT JOIN messages m ON m.chat_id = ch.id
GROUP BY ch.id, ch.recipient_id
"""
AUTHORS_SQL = """
SELECT DISTINCT chat_id, author_id FROM messages WHERE author_id IS NOT NULL
UNION
SELECT m.chat_id, r.author_id FROM reactions r JOIN messages m ON m.id = r.message_id
"""
MESSAGE_SEARCH_SQL = "SELECT chat_id, body, quoted_text, other_text, file_names FROM messages"
MESSAGES_SQL = """
SELECT * FROM messages
WHERE chat_id = ? AND (? OR parent_message_id IS NULL)
  AND (? IS NULL OR date_sent_ms >= ?) AND (? IS NULL OR date_sent_ms < ?)
ORDER BY date_sent_ms, id
"""
ATTACHMENTS_SQL = """
SELECT a.* FROM attachments a JOIN messages m ON m.id = a.message_id
WHERE m.chat_id = ? ORDER BY m.date_sent_ms, m.id, a.id
"""
REACTIONS_SQL = """
SELECT r.* FROM reactions r JOIN messages m ON m.id = r.message_id
WHERE m.chat_id = ? ORDER BY m.date_sent_ms, m.id, r.id
"""
SEARCH_FIELDS = (q.JSON_BODY, q.JSON_QUOTED_TEXT, q.JSON_OTHER_TEXT, q.JSON_FILE_NAMES)


def timestamp_utc(value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value / q.MILLISECONDS_PER_SECOND, UTC).isoformat(
            timespec=q.ISO_TIMESPEC
        )
    except (ValueError, OverflowError, OSError):
        raise BackupError(
            "Database contains a timestamp outside the supported date range."
        ) from None


def parse_date_bound(value: str | None, *, end: bool = False) -> int | None:
    """Use inclusive start/exclusive end bounds; a date-only end includes the entire day."""
    if value is None:
        return None
    try:
        if value.isascii() and value.isdecimal():
            milliseconds = int(value)
            increment = 1
        else:
            parsed = datetime.fromisoformat(value.replace(q.ISO_UTC_SUFFIX, q.ISO_UTC_OFFSET))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            milliseconds = int(parsed.timestamp() * q.MILLISECONDS_PER_SECOND)
            increment = q.MILLISECONDS_PER_DAY if len(value) == q.ISO_DATE_LENGTH else 1
        if milliseconds < 0 or milliseconds > c.MAX_SQLITE_INTEGER - increment:
            raise ValueError
        return milliseconds + increment if end else milliseconds
    except (ValueError, OverflowError, OSError):
        raise BackupError(
            "Dates must be ISO 8601 dates/times or nonnegative epoch milliseconds."
        ) from None


def make_matcher(query: str, *, regex: bool) -> Callable[[str], bool]:
    if regex:
        try:
            compiled = re.compile(query, re.IGNORECASE)
        except re.error as error:
            raise BackupError(f"Invalid regular expression: {error.msg}.") from None
        return lambda value: compiled.search(value) is not None
    folded = query.casefold()
    return lambda value: folded in value.casefold()


def _search_text(row: sqlite3.Row) -> str:
    return q.MATCH_FIELD_SEPARATOR.join(row[field] or "" for field in SEARCH_FIELDS)


def _service_id(value: bytes) -> str:
    if len(value) == c.BACKUP_ID_LENGTH:
        return str(UUID(bytes=value))
    if len(value) == c.BACKUP_ID_LENGTH + q.SERVICE_ID_PREFIX_LENGTH:
        prefix, identifier = value[0], str(UUID(bytes=value[q.SERVICE_ID_PREFIX_LENGTH :]))
        if prefix == q.ACI_SERVICE_ID_PREFIX:
            return identifier
        if prefix == q.PNI_SERVICE_ID_PREFIX:
            return q.PNI_PREFIX + identifier
    return value.hex()


def _recipient_search_names(recipient: proto.Recipient, display_name: str) -> tuple[str, ...]:
    contact = recipient.contact
    names = (
        display_name,
        contact.username,
        contact.systemNickname,
        c.NAME_SEPARATOR.join(filter(None, (contact.profileGivenName, contact.profileFamilyName))),
        c.NAME_SEPARATOR.join(filter(None, (contact.systemGivenName, contact.systemFamilyName))),
        c.NAME_SEPARATOR.join(filter(None, (contact.nickname.given, contact.nickname.family))),
        c.NAME_SEPARATOR.join(filter(None, (contact.sharedName.given, contact.sharedName.family))),
    )
    return tuple(name for name in names if name)


@contextmanager
def open_store(path: Path) -> Iterator[QueryStore]:
    database = path.expanduser().resolve()
    if database.is_dir():
        database /= c.DATABASE_NAME
    if not database.is_file():
        raise BackupError(
            f"Database not found: {database}. Use --database PATH to select an export."
        )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database.as_uri() + q.READ_ONLY_URI_SUFFIX, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute(QUERY_ONLY_SQL)
        connection.execute(BEGIN_READ_SQL)
        if connection.execute(QUERY_SCHEMA_VERSION).fetchone()[0] != c.DATABASE_SCHEMA_VERSION:
            raise BackupError("Unsupported database schema; select a signal-sqlite export.")
        yield QueryStore(connection, database)
    except sqlite3.Error:
        raise BackupError(
            "Cannot query this database; select a readable signal-sqlite export."
        ) from None
    except DecodeError:
        raise BackupError("Database contains an invalid recipient protobuf record.") from None
    finally:
        if connection is not None:
            connection.close()


class QueryStore:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self.connection = connection
        self.path = path
        self.recipients = {row[q.JSON_ID]: row for row in connection.execute(RECIPIENTS_SQL)}
        self.recipient_protos = {
            identifier: proto.Frame.FromString(row[q.COLUMN_RAW_PROTOBUF]).recipient
            for identifier, row in self.recipients.items()
        }
        self.self_ids = {
            identifier
            for identifier, row in self.recipients.items()
            if row[q.JSON_KIND] == c.KIND_SELF
        }
        self._service_ids: dict[str, str] = {}
        for identifier, row in self.recipients.items():
            if row[q.COLUMN_ACI]:
                self._service_ids[row[q.COLUMN_ACI]] = identifier
            if row[q.COLUMN_PNI]:
                self._service_ids[q.PNI_PREFIX + row[q.COLUMN_PNI]] = identifier
        authors: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(AUTHORS_SQL):
            authors[row[q.JSON_CHAT_ID]].add(row[q.JSON_AUTHOR_ID])
        self._channel_search_names: dict[str, tuple[str, ...]] = {}
        self._channels = {
            row[q.JSON_ID]: self._channel(row, authors[row[q.JSON_ID]])
            for row in connection.execute(CHANNELS_SQL)
        }

    def _participant(self, identifier: str) -> dict[str, Any]:
        row = self.recipients.get(identifier)
        return {
            q.JSON_ID: identifier,
            q.JSON_NAME: row[q.COLUMN_DISPLAY_NAME] if row is not None else identifier,
            q.JSON_KIND: row[q.JSON_KIND] if row is not None else c.UNKNOWN_KIND,
        }

    def _channel(self, row: sqlite3.Row, authors: set[str]) -> dict[str, Any]:
        channel_id = row[q.JSON_ID]
        peer_id = row[q.COLUMN_RECIPIENT_ID]
        peer = self.recipients[peer_id]
        is_group = peer[q.JSON_KIND] == c.KIND_GROUP
        identifiers = authors | self.self_ids
        if not is_group:
            identifiers.add(peer_id)
        unknown_members = set()
        if is_group:
            for member in self.recipient_protos[peer_id].group.snapshot.members:
                service_id = _service_id(member.userId)
                if service_id in self._service_ids:
                    identifiers.add(self._service_ids[service_id])
                else:
                    unknown_members.add(service_id)
        participants = [self._participant(identifier) for identifier in identifiers]
        participants.extend(
            {q.JSON_ID: None, q.JSON_NAME: member, q.JSON_KIND: c.UNKNOWN_KIND}
            for member in unknown_members
        )
        participants.sort(
            key=lambda participant: (
                participant[q.JSON_NAME].casefold(),
                participant[q.JSON_ID] or "",
            )
        )
        group_name = peer[q.COLUMN_DISPLAY_NAME] if is_group else None
        search_names = [participant[q.JSON_NAME] for participant in participants]
        if group_name:
            search_names.append(group_name)
        for identifier in identifiers:
            if identifier in self.recipient_protos:
                search_names.extend(
                    _recipient_search_names(
                        self.recipient_protos[identifier],
                        self.recipients[identifier][q.COLUMN_DISPLAY_NAME],
                    )
                )
        self._channel_search_names[channel_id] = tuple(search_names)
        latest = row[q.JSON_LAST_ACTIVITY_MS]
        return {
            q.JSON_ID: channel_id,
            q.JSON_KIND: c.KIND_GROUP if is_group else q.KIND_DIRECT,
            q.JSON_GROUP_NAME: group_name,
            q.JSON_PARTICIPANTS: participants,
            q.JSON_LAST_ACTIVITY_MS: latest,
            q.JSON_LAST_ACTIVITY_UTC: timestamp_utc(latest),
        }

    def channel_page(
        self,
        *,
        page: int = q.DEFAULT_PAGE,
        page_size: int = q.DEFAULT_PAGE_SIZE,
        query: str | None = None,
        regex: bool = False,
        include_messages: bool = False,
    ) -> dict[str, Any]:
        if page < q.MIN_PAGE_NUMBER or page_size < q.MIN_PAGE_SIZE:
            raise BackupError("Page and page size must be positive.")
        channels = list(self._channels.values())
        if query is not None:
            matches = make_matcher(query, regex=regex)
            matching_ids = {
                identifier
                for identifier, names in self._channel_search_names.items()
                if any(matches(name) for name in names)
            }
            if include_messages:
                matching_ids.update(
                    row[q.JSON_CHAT_ID]
                    for row in self.connection.execute(MESSAGE_SEARCH_SQL)
                    if matches(_search_text(row))
                )
            channels = [channel for channel in channels if channel[q.JSON_ID] in matching_ids]
        channels.sort(
            key=lambda channel: (
                -(
                    channel[q.JSON_LAST_ACTIVITY_MS]
                    if channel[q.JSON_LAST_ACTIVITY_MS] is not None
                    else -1
                ),
                int(channel[q.JSON_ID]),
            )
        )
        offset = (page - 1) * page_size
        result = {
            q.JSON_CHANNELS: channels[offset : offset + page_size],
            q.JSON_PAGE: page,
            q.JSON_PAGE_SIZE: page_size,
            q.JSON_TOTAL: len(channels),
            q.JSON_TOTAL_PAGES: (len(channels) + page_size - 1) // page_size,
        }
        if query is not None:
            result.update(
                {
                    q.JSON_QUERY: query,
                    q.JSON_REGEX: regex,
                    q.JSON_INCLUDE_MESSAGES: include_messages,
                }
            )
        return result

    def _attachment(self, row: sqlite3.Row) -> dict[str, Any]:
        relative = row[q.COLUMN_EXPORTED_PATH]
        absolute = None
        if relative is not None:
            target = (self.path.parent / relative).resolve()
            if Path(relative).is_absolute() or not target.is_relative_to(self.path.parent):
                raise BackupError("An attachment path is outside the database's export directory.")
            absolute = str(target)
        fields = (
            q.JSON_ID,
            q.JSON_MESSAGE_ID,
            q.JSON_FIELD_PATH,
            q.JSON_FILE_NAME,
            q.JSON_CONTENT_TYPE,
            q.JSON_CAPTION,
            q.JSON_SIZE_BYTES,
            q.JSON_STATUS,
            q.JSON_ERROR,
        )
        return {
            **{field: row[field] for field in fields},
            q.JSON_RELATIVE_PATH: relative,
            q.JSON_ABSOLUTE_PATH: absolute,
        }

    def conversation(
        self,
        channel_id: str,
        *,
        offset: int = q.DEFAULT_OFFSET,
        limit: int | None = None,
        since: int | None = None,
        until: int | None = None,
        query: str | None = None,
        regex: bool = False,
        include_revisions: bool = False,
    ) -> dict[str, Any]:
        if channel_id not in self._channels:
            raise BackupError(f"Channel {channel_id} was not found.")
        if offset < 0 or (limit is not None and limit < 0):
            raise BackupError("Offset and limit must be nonnegative.")
        if since is not None and until is not None and since >= until:
            raise BackupError("The start date must be earlier than the end date.")
        rows = list(
            self.connection.execute(
                MESSAGES_SQL, (channel_id, include_revisions, since, since, until, until)
            )
        )
        if query is not None:
            matches = make_matcher(query, regex=regex)
            rows = [row for row in rows if matches(_search_text(row))]
        total = len(rows)
        rows = rows[offset : None if limit is None else offset + limit]
        selected_ids = {row[q.JSON_ID] for row in rows}
        attachments = [
            self._attachment(row)
            for row in self.connection.execute(ATTACHMENTS_SQL, (channel_id,))
            if row[q.JSON_MESSAGE_ID] in selected_ids
        ]
        by_message: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for attachment in attachments:
            by_message[attachment[q.JSON_MESSAGE_ID]].append(attachment)
        reactions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for reaction in self.connection.execute(REACTIONS_SQL, (channel_id,)):
            if reaction[q.JSON_MESSAGE_ID] in selected_ids:
                reactions[reaction[q.JSON_MESSAGE_ID]].append(
                    {
                        field: reaction[field]
                        for field in (
                            q.JSON_AUTHOR_ID,
                            q.JSON_EMOJI,
                            q.JSON_SENT_TIMESTAMP_MS,
                            q.JSON_SORT_ORDER,
                        )
                    }
                )
        messages = []
        fields = (
            q.JSON_ID,
            q.JSON_CHAT_ID,
            q.JSON_AUTHOR_ID,
            q.JSON_DATE_SENT_MS,
            q.JSON_DATE_RECEIVED_MS,
            q.JSON_DIRECTION,
            q.JSON_KIND,
            q.JSON_BODY,
            q.JSON_QUOTED_TEXT,
            q.JSON_OTHER_TEXT,
            q.JSON_FILE_NAMES,
            q.JSON_PARENT_MESSAGE_ID,
            q.JSON_REVISION_INDEX,
        )
        for row in rows:
            mine = (
                row[q.JSON_AUTHOR_ID] in self.self_ids
                or row[q.JSON_DIRECTION] == c.DIRECTION_OUTGOING
            )
            author = self.recipients.get(row[q.JSON_AUTHOR_ID])
            messages.append(
                {
                    **{field: row[field] for field in fields},
                    q.JSON_AUTHOR_NAME: author[q.COLUMN_DISPLAY_NAME]
                    if author is not None
                    else (q.SELF_FALLBACK_NAME if mine else q.UNKNOWN_AUTHOR_NAME),
                    q.JSON_AUTHORED_BY_ME: mine,
                    q.JSON_DATE_SENT_UTC: timestamp_utc(row[q.JSON_DATE_SENT_MS]),
                    q.JSON_ATTACHMENTS: by_message[row[q.JSON_ID]],
                    q.JSON_REACTIONS: reactions[row[q.JSON_ID]],
                }
            )
        return {
            q.JSON_CHANNEL: self._channels[channel_id],
            q.JSON_MESSAGES: messages,
            q.JSON_ATTACHMENTS: attachments,
            q.JSON_TOTAL_MESSAGES: total,
            q.JSON_OFFSET: offset,
            q.JSON_LIMIT: limit,
            q.JSON_HAS_MORE: offset + len(messages) < total,
        }

"""Query command names, options, output fields, and presentation defaults."""

from pathlib import Path
from typing import Final

from signal_sqlite import constants as c

COMMAND_CHANNELS: Final = "channels"
ALIAS_CHANNELS: Final = "ch"
COMMAND_LIST: Final = "list"
ALIAS_LIST: Final = "all"
COMMAND_SEARCH: Final = "search"
ALIAS_SEARCH: Final = "match"
COMMAND_CONVERSATIONS: Final = "conversations"
ALIAS_CONVERSATIONS: Final = "convo"
SHORT_ALIAS_CONVERSATIONS: Final = "co"
COMMAND_GET: Final = "get"
ALIAS_GET: Final = "g"
OPTION_FORMAT: Final = "--format"
SHORT_OPTION_FORMAT: Final = "-f"
OPTION_DATABASE: Final = "--database"
SHORT_OPTION_DATABASE: Final = "-d"
OPTION_PAGE: Final = "--page"
OPTION_PAGE_SIZE: Final = "--page-size"
OPTION_REGEX: Final = "--regex"
OPTION_EXPRESSION: Final = "--exp"
SHORT_OPTION_REGEX: Final = "-r"
OPTION_INCLUDE_MESSAGES: Final = "--include-messages"
OPTION_LIMIT: Final = "--limit"
OPTION_OFFSET: Final = "--offset"
OPTION_SINCE: Final = "--since"
OPTION_UNTIL: Final = "--until"
OPTION_SEARCH: Final = "--search"
OPTION_INCLUDE_REVISIONS: Final = "--include-revisions"
PARAM_FORMAT: Final = "output_format"
PARAM_QUERY: Final = "query"
PARAM_CHANNEL_ID: Final = "channel_id"
FORMAT_TEXT: Final = "text"
FORMAT_JSON: Final = "json"
OUTPUT_DIRECTORY: Final = "output"
DEFAULT_DATABASE: Final = Path(OUTPUT_DIRECTORY) / c.DATABASE_NAME
DEFAULT_PAGE: Final = 1
DEFAULT_PAGE_SIZE: Final = 50
MIN_PAGE_NUMBER: Final = 1
MIN_PAGE_SIZE: Final = 1
MAX_PAGE_SIZE: Final = 1000
DEFAULT_OFFSET: Final = 0
MILLISECONDS_PER_SECOND: Final = 1000
MILLISECONDS_PER_DAY: Final = 24 * 60 * 60 * MILLISECONDS_PER_SECOND
ISO_DATE_LENGTH: Final = 10
ISO_UTC_SUFFIX: Final = "Z"
ISO_UTC_OFFSET: Final = "+00:00"
ISO_TIMESPEC: Final = "milliseconds"
READ_ONLY_URI_SUFFIX: Final = "?mode=ro"
PNI_PREFIX: Final = "PNI:"
ACI_SERVICE_ID_PREFIX: Final = 0
PNI_SERVICE_ID_PREFIX: Final = 1
SERVICE_ID_PREFIX_LENGTH: Final = 1
KIND_DIRECT: Final = "direct"
KIND_UPDATE_MESSAGE: Final = "updateMessage"
KIND_DELETED_MESSAGE: Final = "remoteDeletedMessage"
KIND_ADMIN_DELETED_MESSAGE: Final = "adminDeletedMessage"
KIND_STICKER_MESSAGE: Final = "stickerMessage"
KIND_CONTACT_MESSAGE: Final = "contactMessage"
KIND_VIEW_ONCE_MESSAGE: Final = "viewOnceMessage"
KIND_PAYMENT_MESSAGE: Final = "paymentNotification"
KIND_GIFT_BADGE: Final = "giftBadge"
KIND_POLL: Final = "poll"
MESSAGE_KIND_LABELS: Final = {
    c.KIND_STANDARD_MESSAGE: "Message",
    c.KIND_STORY_REPLY: "Story reply",
    KIND_UPDATE_MESSAGE: "Conversation update",
    KIND_DELETED_MESSAGE: "Deleted message",
    KIND_ADMIN_DELETED_MESSAGE: "Message removed by an administrator",
    KIND_STICKER_MESSAGE: "Sticker",
    KIND_CONTACT_MESSAGE: "Shared contact",
    KIND_VIEW_ONCE_MESSAGE: "View-once message",
    KIND_PAYMENT_MESSAGE: "Payment",
    KIND_GIFT_BADGE: "Gift badge",
    KIND_POLL: "Poll",
}
UNKNOWN_MESSAGE_LABEL: Final = "Unsupported message"
SELF_FALLBACK_NAME: Final = "You"
UNKNOWN_AUTHOR_NAME: Final = "Unknown author"
UNKNOWN_TIME: Final = "unknown time"
JSON_CHANNELS: Final = "channels"
JSON_CHANNEL: Final = "channel"
JSON_ID: Final = "id"
JSON_KIND: Final = "kind"
JSON_GROUP_NAME: Final = "group_name"
JSON_PARTICIPANTS: Final = "participants"
JSON_NAME: Final = "name"
JSON_LAST_ACTIVITY_MS: Final = "last_activity_ms"
JSON_LAST_ACTIVITY_UTC: Final = "last_activity_utc"
JSON_PAGE: Final = "page"
JSON_PAGE_SIZE: Final = "page_size"
JSON_TOTAL: Final = "total"
JSON_TOTAL_PAGES: Final = "total_pages"
JSON_QUERY: Final = "query"
JSON_REGEX: Final = "regex"
JSON_INCLUDE_MESSAGES: Final = "include_messages"
JSON_MESSAGES: Final = "messages"
JSON_ATTACHMENTS: Final = "attachments"
JSON_CHAT_ID: Final = "chat_id"
JSON_MESSAGE_ID: Final = "message_id"
JSON_AUTHOR_ID: Final = "author_id"
JSON_AUTHOR_NAME: Final = "author_name"
JSON_AUTHORED_BY_ME: Final = "authored_by_me"
JSON_DATE_SENT_MS: Final = "date_sent_ms"
JSON_DATE_SENT_UTC: Final = "date_sent_utc"
JSON_DATE_RECEIVED_MS: Final = "date_received_ms"
JSON_DIRECTION: Final = "direction"
JSON_BODY: Final = "body"
JSON_QUOTED_TEXT: Final = "quoted_text"
JSON_OTHER_TEXT: Final = "other_text"
JSON_FILE_NAMES: Final = "file_names"
JSON_PARENT_MESSAGE_ID: Final = "parent_message_id"
JSON_REVISION_INDEX: Final = "revision_index"
JSON_REACTIONS: Final = "reactions"
JSON_EMOJI: Final = "emoji"
JSON_SENT_TIMESTAMP_MS: Final = "sent_timestamp_ms"
JSON_SORT_ORDER: Final = "sort_order"
JSON_FIELD_PATH: Final = "field_path"
JSON_FILE_NAME: Final = "file_name"
JSON_CONTENT_TYPE: Final = "content_type"
JSON_CAPTION: Final = "caption"
JSON_SIZE_BYTES: Final = "size_bytes"
JSON_RELATIVE_PATH: Final = "relative_path"
JSON_ABSOLUTE_PATH: Final = "absolute_path"
JSON_STATUS: Final = "status"
JSON_ERROR: Final = "error"
JSON_TOTAL_MESSAGES: Final = "total_messages"
JSON_OFFSET: Final = "offset"
JSON_LIMIT: Final = "limit"
JSON_HAS_MORE: Final = "has_more"
JSON_DATABASE: Final = "database"
JSON_SNAPSHOT: Final = "snapshot"
COLUMN_RECIPIENT_ID: Final = "recipient_id"
COLUMN_DISPLAY_NAME: Final = "display_name"
COLUMN_ACI: Final = "aci"
COLUMN_PNI: Final = "pni"
COLUMN_USERNAME: Final = "username"
COLUMN_RAW_PROTOBUF: Final = "raw_protobuf"
COLUMN_EXPORTED_PATH: Final = "exported_path"
DEFAULT_TERMINAL_COLUMNS: Final = 100
DEFAULT_TERMINAL_LINES: Final = 24
MIN_TERMINAL_COLUMNS: Final = 20
MESSAGE_WIDTH_FRACTION: Final = 0.8
TEXT_INDENT: Final = "  "
TEXT_QUOTE_PREFIX: Final = "> "
TEXT_ATTACHMENT_PREFIX: Final = "Attachment: "
TEXT_REACTION_PREFIX: Final = "Reactions: "
TEXT_REVISION_PREFIX: Final = "Revision: "
TEXT_TAB_WIDTH: Final = 4
UNICODE_WIDE_CATEGORIES: Final = frozenset({"W", "F"})
UNICODE_CONTROL_CATEGORY: Final = "C"
UNICODE_ESCAPE_ENCODING: Final = "unicode_escape"
ASCII_ENCODING: Final = "ascii"
CARRIAGE_RETURN: Final = "\r"
CRLF: Final = "\r\n"
TAB: Final = "\t"
SPACE: Final = " "
JOIN_NAMES: Final = ", "
MATCH_FIELD_SEPARATOR: Final = "\n"

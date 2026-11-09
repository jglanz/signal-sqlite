"""Human-readable channel summaries and terminal-width-aware chat rendering."""

import json
import unicodedata
from typing import Any

from signal_sqlite import constants as c
from signal_sqlite import query_constants as q


def json_output(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=c.JSON_INDENT, ensure_ascii=False)


def safe_text(value: str) -> str:
    value = value.replace(q.CRLF, c.TEXT_SEPARATOR).replace(q.CARRIAGE_RETURN, c.TEXT_SEPARATOR)
    return "".join(
        char
        if char in (c.TEXT_SEPARATOR, q.TAB)
        or not unicodedata.category(char).startswith(q.UNICODE_CONTROL_CATEGORY)
        else char.encode(q.UNICODE_ESCAPE_ENCODING).decode(q.ASCII_ENCODING)
        for char in value
    ).expandtabs(q.TEXT_TAB_WIDTH)


def display_width(value: str) -> int:
    return sum(
        0
        if unicodedata.combining(char)
        else (2 if unicodedata.east_asian_width(char) in q.UNICODE_WIDE_CATEGORIES else 1)
        for char in value
    )


def _wrapped_lines(value: str, width: int) -> list[str]:
    result = []
    for line in safe_text(value).split(c.TEXT_SEPARATOR):
        chunk = ""
        columns = 0
        for char in line:
            char_width = display_width(char)
            if chunk and columns + char_width > width:
                result.append(chunk)
                chunk = ""
                columns = 0
            chunk += char
            columns += char_width
        result.append(chunk)
    return result


def _channel_header(channel: dict[str, Any]) -> list[str]:
    names = q.JOIN_NAMES.join(person[q.JSON_NAME] for person in channel[q.JSON_PARTICIPANTS])
    lines = [f"Channel {channel[q.JSON_ID]} ({channel[q.JSON_KIND]})"]
    if channel[q.JSON_GROUP_NAME] is not None:
        lines.append(f"Group: {channel[q.JSON_GROUP_NAME]}")
    lines.append(f"Participants: {names}")
    lines.append(f"Latest: {channel[q.JSON_LAST_ACTIVITY_UTC] or q.UNKNOWN_TIME}")
    return [safe_text(line) for line in lines]


def render_channels(payload: dict[str, Any]) -> str:
    blocks = [
        c.TEXT_SEPARATOR.join(_channel_header(channel)) for channel in payload[q.JSON_CHANNELS]
    ]
    if not blocks:
        blocks.append("No channels found.")
    blocks.append(
        f"Page {payload[q.JSON_PAGE]} / {payload[q.JSON_TOTAL_PAGES]} · "
        f"{payload[q.JSON_TOTAL]} channels · page size {payload[q.JSON_PAGE_SIZE]}"
    )
    return (c.TEXT_SEPARATOR * 2).join(blocks)


def message_text_lines(message: dict[str, Any]) -> list[str]:
    author = message[q.JSON_AUTHOR_NAME]
    if message[q.JSON_AUTHORED_BY_ME]:
        author = f"You ({author})"
    lines = [f"{author} · {message[q.JSON_DATE_SENT_UTC] or q.UNKNOWN_TIME}"]
    if message[q.JSON_PARENT_MESSAGE_ID] is not None:
        lines.append(q.TEXT_REVISION_PREFIX + str(message[q.JSON_PARENT_MESSAGE_ID]))
    quoted = message[q.JSON_QUOTED_TEXT]
    if quoted:
        lines.extend(q.TEXT_QUOTE_PREFIX + line for line in quoted.split(c.TEXT_SEPARATOR))
    kind_label = q.MESSAGE_KIND_LABELS.get(message[q.JSON_KIND], q.UNKNOWN_MESSAGE_LABEL)
    lines.append(message[q.JSON_BODY] or f"[{kind_label}]")
    attachment_text = {
        attachment[field]
        for attachment in message[q.JSON_ATTACHMENTS]
        for field in (q.JSON_FILE_NAME, q.JSON_CONTENT_TYPE, q.JSON_CAPTION)
        if attachment[field]
    }
    attachment_text.update(reaction[q.JSON_EMOJI] for reaction in message[q.JSON_REACTIONS])
    extra = message[q.JSON_OTHER_TEXT]
    if extra:
        lines.extend(line for line in extra.split(c.TEXT_SEPARATOR) if line not in attachment_text)
    for attachment in message[q.JSON_ATTACHMENTS]:
        path = attachment[q.JSON_ABSOLUTE_PATH]
        if path is not None:
            lines.append(q.TEXT_ATTACHMENT_PREFIX + path)
        else:
            lines.append(
                q.TEXT_ATTACHMENT_PREFIX
                + f"[{attachment[q.JSON_STATUS]}] "
                + (attachment[q.JSON_FILE_NAME] or attachment[q.JSON_CONTENT_TYPE] or "unnamed")
            )
        if attachment[q.JSON_CAPTION]:
            lines.append(attachment[q.JSON_CAPTION])
    if message[q.JSON_REACTIONS]:
        lines.append(
            q.TEXT_REACTION_PREFIX
            + q.JOIN_NAMES.join(reaction[q.JSON_EMOJI] for reaction in message[q.JSON_REACTIONS])
        )
    return lines


def render_conversation(payload: dict[str, Any], *, width: int) -> str:
    width = max(q.MIN_TERMINAL_COLUMNS, width)
    bubble_width = max(1, int(width * q.MESSAGE_WIDTH_FRACTION))
    blocks = [c.TEXT_SEPARATOR.join(_channel_header(payload[q.JSON_CHANNEL]))]
    for message in payload[q.JSON_MESSAGES]:
        lines = [
            line
            for part in message_text_lines(message)
            for line in (
                [safe_text(part)]
                if part.startswith(q.TEXT_ATTACHMENT_PREFIX)
                else _wrapped_lines(part, bubble_width)
            )
        ]
        if message[q.JSON_AUTHORED_BY_ME]:
            lines = [q.SPACE * max(0, width - display_width(line)) + line for line in lines]
        blocks.append(c.TEXT_SEPARATOR.join(lines))
    if not payload[q.JSON_MESSAGES]:
        blocks.append("No messages match the requested range.")
    blocks.append(
        f"Showing {len(payload[q.JSON_MESSAGES])} of {payload[q.JSON_TOTAL_MESSAGES]} messages "
        f"(offset {payload[q.JSON_OFFSET]})"
    )
    return (c.TEXT_SEPARATOR * 2).join(blocks)

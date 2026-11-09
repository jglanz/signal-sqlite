from conftest import (
    CHAT_ID,
    QUERY_ALICE_NAME,
    QUERY_BOB_NAME,
    QUERY_GROUP_CHAT_ID,
    QUERY_GROUP_NAME,
    QUERY_OWN_TEXT,
    TEST_TEXT,
)

from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite.query import open_store
from signal_sqlite.render import display_width, render_conversation, safe_text

TERMINAL_WIDTH = 80
NARROW_TERMINAL_WIDTH = 30
WIDE_TEXT = "你好，世界！ café a\u0301"
CONTROL_TEXT = "synthetic\x1b[31m text\x00"
ESCAPED_CONTROL = "\\x1b"
ESCAPE_CHARACTER = "\x1b"
TEST_CONTROL_NEWLINES = "first\r\nsecond\rthird\tfourth"
EXPECTED_CONTROL_NEWLINES = "first\nsecond\nthird"
JSON_ONLY_LABEL = "relative_path"
STANDALONE_ATTACHMENTS_LABEL = "Attachments:"


def test_text_channel_summary_contains_every_required_field(query_export, invoke_query):
    result = invoke_query(
        query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST, output_format=q.FORMAT_TEXT
    )
    assert result.exit_code == 0, result.output
    assert str(QUERY_GROUP_CHAT_ID) in result.stdout
    assert QUERY_GROUP_NAME in result.stdout
    assert QUERY_ALICE_NAME in result.stdout
    assert QUERY_BOB_NAME in result.stdout
    assert "Latest:" in result.stdout
    assert q.UNKNOWN_TIME in result.stdout


def test_text_conversation_has_chat_alignment_and_inline_absolute_paths(query_export, invoke_query):
    result = invoke_query(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        output_format=q.FORMAT_TEXT,
        terminal_width=TERMINAL_WIDTH,
    )
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    incoming = next(line for line in lines if line == TEST_TEXT)
    outgoing = next(line for line in lines if line.strip() == QUERY_OWN_TEXT)
    assert incoming == incoming.lstrip()
    assert outgoing.startswith(q.SPACE)
    assert display_width(outgoing) == TERMINAL_WIDTH
    assert JSON_ONLY_LABEL not in result.stdout
    assert STANDALONE_ATTACHMENTS_LABEL not in result.stdout
    with open_store(query_export.output) as store:
        conversation = store.conversation(str(CHAT_ID))
    for attachment in conversation[q.JSON_ATTACHMENTS]:
        if attachment[q.JSON_ABSOLUTE_PATH]:
            assert attachment[q.JSON_ABSOLUTE_PATH] in result.stdout
    assert "[unavailable]" in result.stdout


def test_wrapped_unicode_messages_stay_right_aligned(query_export):
    with open_store(query_export.output) as store:
        conversation = store.conversation(str(CHAT_ID), offset=1)
    message = conversation[q.JSON_MESSAGES][0]
    message[q.JSON_BODY] = WIDE_TEXT
    message[q.JSON_QUOTED_TEXT] = ""
    message[q.JSON_OTHER_TEXT] = ""
    message[q.JSON_ATTACHMENTS] = []
    message[q.JSON_REACTIONS] = []
    rendered = render_conversation(conversation, width=NARROW_TERMINAL_WIDTH)
    blocks = rendered.split(c.TEXT_SEPARATOR * 2)
    assert blocks[1].startswith(q.SPACE)
    assert all(display_width(line) == NARROW_TERMINAL_WIDTH for line in blocks[1].splitlines())
    assert "你好" in rendered


def test_control_characters_cannot_change_terminal_formatting():
    rendered = safe_text(CONTROL_TEXT)
    assert ESCAPED_CONTROL in rendered
    assert ESCAPE_CHARACTER not in rendered
    assert safe_text(TEST_CONTROL_NEWLINES).startswith(EXPECTED_CONTROL_NEWLINES)


def test_changes_have_human_readable_labels(query_export, invoke_query):
    result = invoke_query(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(QUERY_GROUP_CHAT_ID),
        output_format=q.FORMAT_TEXT,
    )
    assert result.exit_code == 0, result.output
    assert "[Conversation update]" in result.stdout

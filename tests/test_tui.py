import asyncio
import hashlib
import json
import stat

import pytest
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Select,
    Static,
    TabbedContent,
    TextArea,
)

from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite import tui_constants as t
from signal_sqlite.tui import MessageCard, SignalSQLiteApp, save_output, selector

TEST_SCREEN_SIZE = (120, 40)
TEST_SMALL_SCREEN_SIZE = (80, 24)
EXPECTED_CHANNELS = 3
DIRECT_CHANNEL_ROW = 1
DIRECT_CHANNEL_ID = "10"
KEY_ENTER = "enter"
KEY_QUIT = "q"
SEARCH_MISS = "no channel has this name"
SEARCH_MATCH = "announcement"
SEARCH_INVALID = "["
SAVED_FILENAME = "conversation.json"
SAVE_DIRECTORY = "results"
INVALID_DATABASE_NAME = "missing.sqlite3"
EVENT_PAD_SECONDS = 0.1
CHANNEL_PAGE_SIZE_ATTRIBUTE = "CHANNEL_PAGE_SIZE"
MESSAGE_PAGE_SIZE_ATTRIBUTE = "MESSAGE_PAGE_SIZE"
SINGLE_ITEM_PAGE_SIZE = 1


def test_headless_channel_selection_and_message_bubbles(query_export):
    async def scenario():
        app = SignalSQLiteApp(query_export.output)
        async with app.run_test(size=TEST_SCREEN_SIZE) as pilot:
            await pilot.pause()
            table = app.query_one(selector(t.ID_CHANNEL_TABLE), DataTable)
            assert table.row_count == EXPECTED_CHANNELS
            assert app.channel_result[q.JSON_TOTAL] == EXPECTED_CHANNELS
            table.move_cursor(row=DIRECT_CHANNEL_ROW)
            await pilot.press(KEY_ENTER)
            await pilot.pause()
            assert app.selected_channel_id == DIRECT_CHANNEL_ID
            assert app.query_one(selector(t.ID_TABS), TabbedContent).active == t.ID_CONVERSATION_TAB
            cards = list(app.query(MessageCard))
            assert len(cards) == 2
            own = next(card for card in cards if card.message[q.JSON_AUTHORED_BY_ME])
            other = next(card for card in cards if not card.message[q.JSON_AUTHORED_BY_ME])
            assert own.has_class(t.CLASS_MINE)
            assert not other.has_class(t.CLASS_MINE)
            assert own.styles.align_horizontal == t.ALIGNMENT_RIGHT
            assert other.styles.align_horizontal == t.ALIGNMENT_LEFT
            await pilot.press(t.KEY_CHANNELS)
            assert app.query_one(selector(t.ID_TABS), TabbedContent).active == t.ID_CHANNEL_TAB
            await pilot.press(KEY_QUIT)

    asyncio.run(scenario())


def test_headless_search_errors_and_empty_results(query_export):
    async def scenario():
        app = SignalSQLiteApp(query_export.output)
        async with app.run_test(size=TEST_SCREEN_SIZE) as pilot:
            await pilot.pause()
            search = app.query_one(selector(t.ID_CHANNEL_SEARCH), Input)
            search.value = SEARCH_MATCH
            app.query_one(selector(t.ID_INCLUDE_MESSAGES), Checkbox).value = True
            await pilot.pause(t.SEARCH_DELAY_SECONDS + EVENT_PAD_SECONDS)
            assert app.channel_result[q.JSON_TOTAL] == 1
            search.value = SEARCH_INVALID
            app.query_one(selector(t.ID_CHANNEL_REGEX), Checkbox).value = True
            await pilot.pause(t.SEARCH_DELAY_SECONDS + EVENT_PAD_SECONDS)
            assert "Invalid regular expression" in str(
                app.query_one(selector(t.ID_STATUS), Static).content
            )
            app.query_one(selector(t.ID_CHANNEL_REGEX), Checkbox).value = False
            search.value = SEARCH_MISS
            await pilot.pause(t.SEARCH_DELAY_SECONDS + EVENT_PAD_SECONDS)
            assert app.channel_result[q.JSON_TOTAL] == 0
            assert app.conversation_result is None
            assert app.query_one(selector(t.ID_SAVE_OUTPUT), Button).disabled

    asyncio.run(scenario())


def test_headless_output_preview_and_private_save(query_export, tmp_path):
    async def scenario():
        database = query_export.output / c.DATABASE_NAME
        before = hashlib.sha256(database.read_bytes()).digest()
        app = SignalSQLiteApp(database)
        async with app.run_test(size=TEST_SCREEN_SIZE) as pilot:
            await pilot.pause()
            await pilot.press(t.KEY_OUTPUT)
            assert app.query_one(selector(t.ID_TABS), TabbedContent).active == t.ID_OUTPUT_TAB
            app.query_one(selector(t.ID_OUTPUT_FORMAT), Select).value = q.FORMAT_JSON
            await pilot.pause()
            preview = app.query_one(selector(t.ID_OUTPUT_PREVIEW), TextArea)
            assert preview.read_only
            payload = json.loads(preview.text)
            assert q.JSON_MESSAGES in payload and q.JSON_ATTACHMENTS in payload
            target = tmp_path / SAVE_DIRECTORY / SAVED_FILENAME
            app.query_one(selector(t.ID_OUTPUT_PATH), Input).value = str(target)
            assert await pilot.click(selector(t.ID_SAVE_OUTPUT))
            await pilot.pause()
            assert json.loads(target.read_text(encoding=c.UTF8)) == payload
            assert stat.S_IMODE(target.stat().st_mode) == c.FILE_MODE
            assert stat.S_IMODE(target.parent.stat().st_mode) == c.DIRECTORY_MODE
            app.query_one(selector(t.ID_OUTPUT_SOURCE), Select).value = t.SOURCE_CHANNELS
            await pilot.pause()
            assert json.loads(preview.text)[q.JSON_TOTAL] == EXPECTED_CHANNELS
            assert hashlib.sha256(database.read_bytes()).digest() == before

    asyncio.run(scenario())


def test_tui_small_terminal_and_filters(query_export):
    async def scenario():
        app = SignalSQLiteApp(query_export.output)
        async with app.run_test(size=TEST_SMALL_SCREEN_SIZE) as pilot:
            await pilot.pause()
            app.selected_channel_id = DIRECT_CHANNEL_ID
            app.query_one(selector(t.ID_CONVERSATION_SEARCH), Input).value = SEARCH_MISS
            await app.load_conversation(reset=True)
            assert app.conversation_result[q.JSON_MESSAGES] == []
            app.query_one(selector(t.ID_CONVERSATION_SEARCH), Input).value = ""
            app.query_one(selector(t.ID_INCLUDE_REVISIONS), Checkbox).value = True
            await app.load_conversation(reset=True)
            assert len(app.conversation_result[q.JSON_MESSAGES]) == 3
            app.query_one(selector(t.ID_SINCE), Input).value = "invalid date"
            await app.load_conversation(reset=True)
            assert "Dates must be" in str(app.query_one(selector(t.ID_STATUS), Static).content)
            assert app.query_one(selector(t.ID_CHANNEL_TABLE), DataTable).region.width > 0

    asyncio.run(scenario())


def test_save_output_never_overwrites(tmp_path):
    path = tmp_path / SAVED_FILENAME
    saved = save_output(path, "first")
    assert saved == path
    with pytest.raises(FileExistsError):
        save_output(path, "replacement")
    assert path.read_text(encoding=c.UTF8) == "first\n"


def test_tui_command_registered_and_rejects_noninteractive(query_export, invoke_query):
    result = invoke_query(query_export, t.COMMAND_TUI)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "interactive terminal" in result.stderr
    missing = invoke_query(
        query_export, t.COMMAND_TUI, database=query_export.output / INVALID_DATABASE_NAME
    )
    assert missing.exit_code == c.EXIT_INCOMPLETE
    assert "Database not found" in missing.stderr


def test_headless_channel_and_message_pagination(query_export, monkeypatch):
    monkeypatch.setattr(t, CHANNEL_PAGE_SIZE_ATTRIBUTE, SINGLE_ITEM_PAGE_SIZE)
    monkeypatch.setattr(t, MESSAGE_PAGE_SIZE_ATTRIBUTE, SINGLE_ITEM_PAGE_SIZE)

    async def scenario():
        app = SignalSQLiteApp(query_export.output)
        async with app.run_test(size=TEST_SCREEN_SIZE) as pilot:
            await pilot.pause()
            assert app.channel_result[q.JSON_PAGE] == q.DEFAULT_PAGE
            await pilot.press(KEY_ENTER)
            await pilot.pause()
            assert app.message_offset == 1
            assert await pilot.click(selector(t.ID_MESSAGE_PREVIOUS))
            await pilot.pause()
            assert app.message_offset == 0
            assert app.conversation_result[q.JSON_HAS_MORE]
            assert await pilot.click(selector(t.ID_MESSAGE_LATEST))
            await pilot.pause()
            assert app.message_offset == 1
            await pilot.press(t.KEY_CHANNELS)
            assert await pilot.click(selector(t.ID_CHANNEL_NEXT))
            await pilot.pause()
            assert app.channel_result[q.JSON_PAGE] == q.DEFAULT_PAGE + 1
            assert app.selected_channel_id == DIRECT_CHANNEL_ID
            assert await pilot.click(selector(t.ID_CHANNEL_PREVIOUS))
            await pilot.pause()
            assert app.channel_result[q.JSON_PAGE] == q.DEFAULT_PAGE

    asyncio.run(scenario())


def test_missing_result_cannot_be_saved(query_export, tmp_path):
    async def scenario():
        app = SignalSQLiteApp(query_export.output)
        async with app.run_test(size=TEST_SCREEN_SIZE) as pilot:
            await pilot.pause()
            app.query_one(selector(t.ID_SINCE), Input).value = "invalid date"
            await app.load_conversation(reset=True)
            assert app.conversation_result is None
            target = tmp_path / SAVED_FILENAME
            app.query_one(selector(t.ID_OUTPUT_PATH), Input).value = str(target)
            app.action_save_output()
            assert not target.exists()

    asyncio.run(scenario())

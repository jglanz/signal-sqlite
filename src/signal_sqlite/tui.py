"""Textual channel browser, conversation viewer, and result output."""

from pathlib import Path
from typing import Any

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite import tui_constants as t
from signal_sqlite.backup import private_umask
from signal_sqlite.query import open_store, parse_date_bound
from signal_sqlite.render import (
    json_output,
    message_text_lines,
    render_channels,
    render_conversation,
    safe_text,
)


def selector(identifier: str) -> str:
    return t.SELECTOR_PREFIX + identifier


def save_output(path: Path, content: str) -> Path:
    """Save one private output file, including missing parents, without overwriting."""
    target = path.expanduser().absolute()
    with private_umask():
        target.parent.mkdir(parents=True, exist_ok=True, mode=c.DIRECTORY_MODE)
        with target.open(t.OUTPUT_EXCLUSIVE_WRITE_MODE, encoding=c.UTF8) as stream:
            stream.write(content)
            if not content.endswith(c.TEXT_SEPARATOR):
                stream.write(c.TEXT_SEPARATOR)
    return target


class MessageCard(Vertical):
    def __init__(self, message: dict[str, Any]) -> None:
        classes = t.CLASS_MESSAGE_ROW
        if message[q.JSON_AUTHORED_BY_ME]:
            classes += q.SPACE + t.CLASS_MINE
        super().__init__(classes=classes, id=t.MESSAGE_ID_PREFIX + str(message[q.JSON_ID]))
        self.message = message

    def compose(self) -> ComposeResult:
        lines = message_text_lines(self.message)
        text = Text()
        for index, line in enumerate(lines):
            if index:
                text.append(c.TEXT_SEPARATOR)
            text.append(safe_text(line), style=t.RICH_AUTHOR_STYLE if index == 0 else "")
        yield Static(text, classes=t.CLASS_MESSAGE_BUBBLE)


class SignalSQLiteApp(App[None]):
    TITLE = t.APP_TITLE
    SUB_TITLE = t.APP_SUBTITLE
    CSS_PATH = t.CSS_FILENAME
    BINDINGS = [
        Binding(t.KEY_QUIT, t.ACTION_QUIT, "Quit"),
        Binding(t.KEY_SEARCH, t.ACTION_SEARCH, "Find channel", priority=True),
        Binding(t.KEY_CHANNELS, t.ACTION_CHANNELS, "Channels"),
        Binding(t.KEY_REFRESH, t.ACTION_REFRESH, "Refresh", priority=True),
        Binding(t.KEY_OUTPUT, t.ACTION_OUTPUT, "Output", priority=True),
    ]

    def __init__(self, database: Path, *, output_format: str = q.FORMAT_TEXT) -> None:
        super().__init__()
        self.database = database
        self.output_format = output_format
        self.channel_result: dict[str, Any] | None = None
        self.conversation_result: dict[str, Any] | None = None
        self.selected_channel_id: str | None = None
        self.channel_page = q.DEFAULT_PAGE
        self.message_offset = q.DEFAULT_OFFSET
        self._search_timer: Timer | None = None
        self._views_ready = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(str(self.database), id=t.ID_DATABASE, markup=False)
        with TabbedContent(id=t.ID_TABS):
            with TabPane("Channels", id=t.ID_CHANNEL_TAB):
                yield Input(placeholder="Find participants or groups…", id=t.ID_CHANNEL_SEARCH)
                with Horizontal(classes=t.CLASS_TOOLBAR):
                    yield Checkbox("Regex", id=t.ID_CHANNEL_REGEX)
                    yield Checkbox("Include messages", id=t.ID_INCLUDE_MESSAGES)
                yield DataTable(
                    id=t.ID_CHANNEL_TABLE, cursor_type=t.TABLE_CURSOR_ROW, zebra_stripes=True
                )
                yield Static("", id=t.ID_CHANNEL_STATUS, markup=False)
                with Horizontal(classes=t.CLASS_TOOLBAR):
                    yield Button("Previous", id=t.ID_CHANNEL_PREVIOUS)
                    yield Button("Next", id=t.ID_CHANNEL_NEXT)
                    yield Static("Enter opens the selected channel.", markup=False)
            with TabPane("Conversation", id=t.ID_CONVERSATION_TAB):
                yield Static(t.EMPTY_MESSAGE, id=t.ID_CONVERSATION_HEADER, markup=False)
                with Collapsible(title="Message filters", id=t.ID_FILTERS):
                    yield Input(placeholder="Search message text…", id=t.ID_CONVERSATION_SEARCH)
                    with Horizontal(classes=t.CLASS_TOOLBAR):
                        yield Checkbox("Regex", id=t.ID_CONVERSATION_REGEX)
                        yield Checkbox("Include revisions", id=t.ID_INCLUDE_REVISIONS)
                    with Horizontal(classes=t.CLASS_TOOLBAR):
                        yield Input(placeholder="Since (UTC date/time)", id=t.ID_SINCE)
                        yield Input(placeholder="Until (UTC date/time)", id=t.ID_UNTIL)
                    with Horizontal(classes=t.CLASS_TOOLBAR):
                        yield Button("Apply", id=t.ID_APPLY_FILTERS, variant=t.COLOR_PRIMARY)
                        yield Button("Reset", id=t.ID_RESET_FILTERS)
                yield VerticalScroll(id=t.ID_MESSAGE_LIST)
                yield Static("", id=t.ID_MESSAGE_STATUS, markup=False)
                with Horizontal(classes=t.CLASS_TOOLBAR):
                    yield Button("Older", id=t.ID_MESSAGE_PREVIOUS)
                    yield Button("Newer", id=t.ID_MESSAGE_NEXT)
                    yield Button("Latest", id=t.ID_MESSAGE_LATEST)
            with TabPane("Output", id=t.ID_OUTPUT_TAB):
                with Horizontal(classes=t.CLASS_TOOLBAR):
                    yield Select(
                        [
                            ("Conversation", t.SOURCE_CONVERSATION),
                            ("Channel page", t.SOURCE_CHANNELS),
                        ],
                        value=t.SOURCE_CONVERSATION,
                        allow_blank=False,
                        id=t.ID_OUTPUT_SOURCE,
                    )
                    yield Select(
                        [("Text", q.FORMAT_TEXT), ("JSON", q.FORMAT_JSON)],
                        value=self.output_format,
                        allow_blank=False,
                        id=t.ID_OUTPUT_FORMAT,
                    )
                yield TextArea(read_only=True, show_line_numbers=False, id=t.ID_OUTPUT_PREVIEW)
                with Horizontal(classes=t.CLASS_TOOLBAR):
                    yield Input(placeholder="Save to a new file…", id=t.ID_OUTPUT_PATH)
                    yield Button("Save", id=t.ID_SAVE_OUTPUT, variant=t.COLOR_SUCCESS)
        yield Static(
            "Read-only database · select a channel and press Enter", id=t.ID_STATUS, markup=False
        )
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one(selector(t.ID_CHANNEL_TABLE), DataTable)
        table.add_column(t.COLUMN_ID, width=t.TABLE_ID_WIDTH)
        table.add_column(t.COLUMN_CHANNEL, width=t.TABLE_NAME_WIDTH)
        table.add_column(t.COLUMN_LATEST)
        self._views_ready = True
        await self.load_channels()
        table.focus()

    def _value(self, identifier: str) -> str:
        return self.query_one(selector(identifier), Input).value.strip()

    def _checked(self, identifier: str) -> bool:
        return self.query_one(selector(identifier), Checkbox).value

    def _status(self, message: str, *, error: bool = False) -> None:
        self.query_one(selector(t.ID_STATUS), Static).update(safe_text(message))
        if error:
            self.notify(safe_text(message), severity=t.SEVERITY_ERROR)

    async def load_channels(self) -> None:
        try:
            query = self._value(t.ID_CHANNEL_SEARCH) or None
            with open_store(self.database) as store:
                result = store.channel_page(
                    page=self.channel_page,
                    page_size=t.CHANNEL_PAGE_SIZE,
                    query=query,
                    regex=self._checked(t.ID_CHANNEL_REGEX),
                    include_messages=self._checked(t.ID_INCLUDE_MESSAGES),
                )
        except (BackupError, OSError) as error:
            self._status(str(error), error=True)
            return
        self.channel_result = result
        table = self.query_one(selector(t.ID_CHANNEL_TABLE), DataTable)
        table.clear()
        for channel in result[q.JSON_CHANNELS]:
            names = q.JOIN_NAMES.join(
                person[q.JSON_NAME] for person in channel[q.JSON_PARTICIPANTS]
            )
            label = channel[q.JSON_GROUP_NAME] or names
            if channel[q.JSON_GROUP_NAME]:
                label += c.TEXT_SEPARATOR + names
            latest = channel[q.JSON_LAST_ACTIVITY_UTC]
            table.add_row(
                channel[q.JSON_ID],
                Text(safe_text(label)),
                latest[: t.TIMESTAMP_DISPLAY_LENGTH] if latest else q.UNKNOWN_TIME,
                key=channel[q.JSON_ID],
                height=2 if channel[q.JSON_GROUP_NAME] else 1,
            )
        self.query_one(selector(t.ID_CHANNEL_STATUS), Static).update(
            f"Page {result[q.JSON_PAGE]} / {result[q.JSON_TOTAL_PAGES]} · "
            f"{result[q.JSON_TOTAL]} channels"
        )
        self.query_one(selector(t.ID_CHANNEL_PREVIOUS), Button).disabled = (
            self.channel_page <= q.DEFAULT_PAGE
        )
        self.query_one(selector(t.ID_CHANNEL_NEXT), Button).disabled = (
            self.channel_page >= result[q.JSON_TOTAL_PAGES]
        )
        identifiers = {channel[q.JSON_ID] for channel in result[q.JSON_CHANNELS]}
        changed = self.selected_channel_id not in identifiers
        if changed:
            self.selected_channel_id = (
                result[q.JSON_CHANNELS][0][q.JSON_ID] if identifiers else None
            )
        if self.selected_channel_id is not None:
            if not await self.load_conversation(latest=changed):
                return
        else:
            await self.clear_conversation()
        self.update_output()
        self._status("Enter opens a channel · Esc returns to channels · Ctrl+E shows output")

    async def clear_conversation(self) -> None:
        self.conversation_result = None
        await self.query_one(selector(t.ID_MESSAGE_LIST), VerticalScroll).remove_children()
        self.query_one(selector(t.ID_CONVERSATION_HEADER), Static).update(t.EMPTY_MESSAGE)
        self.query_one(selector(t.ID_MESSAGE_STATUS), Static).update("")
        for identifier in (t.ID_MESSAGE_PREVIOUS, t.ID_MESSAGE_NEXT, t.ID_MESSAGE_LATEST):
            self.query_one(selector(identifier), Button).disabled = True
        self.update_output()

    async def load_conversation(self, *, latest: bool = False, reset: bool = False) -> bool:
        if self.selected_channel_id is None:
            return False
        if reset:
            self.message_offset = q.DEFAULT_OFFSET
        try:
            since = parse_date_bound(self._value(t.ID_SINCE) or None)
            until = parse_date_bound(self._value(t.ID_UNTIL) or None, end=True)
            query = self._value(t.ID_CONVERSATION_SEARCH) or None
            regex = self._checked(t.ID_CONVERSATION_REGEX)
            if regex and query is None:
                raise BackupError("Enter message search text to use Regex.")
            with open_store(self.database) as store:
                filters = dict(
                    since=since,
                    until=until,
                    query=query,
                    regex=regex,
                    include_revisions=self._checked(t.ID_INCLUDE_REVISIONS),
                )
                result = store.conversation(
                    self.selected_channel_id,
                    offset=self.message_offset,
                    limit=t.MESSAGE_PAGE_SIZE,
                    **filters,
                )
                if latest:
                    self.message_offset = max(
                        q.DEFAULT_OFFSET, result[q.JSON_TOTAL_MESSAGES] - t.MESSAGE_PAGE_SIZE
                    )
                    result = store.conversation(
                        self.selected_channel_id,
                        offset=self.message_offset,
                        limit=t.MESSAGE_PAGE_SIZE,
                        **filters,
                    )
        except (BackupError, OSError) as error:
            await self.clear_conversation()
            self._status(str(error), error=True)
            return False
        self.conversation_result = result
        channel = result[q.JSON_CHANNEL]
        names = q.JOIN_NAMES.join(person[q.JSON_NAME] for person in channel[q.JSON_PARTICIPANTS])
        title = channel[q.JSON_GROUP_NAME] or names
        header = c.TEXT_SEPARATOR.join(
            (
                f"Channel {channel[q.JSON_ID]} · {title}",
                f"Participants: {names}",
                f"Latest: {channel[q.JSON_LAST_ACTIVITY_UTC] or q.UNKNOWN_TIME}",
            )
        )
        self.query_one(selector(t.ID_CONVERSATION_HEADER), Static).update(safe_text(header))
        container = self.query_one(selector(t.ID_MESSAGE_LIST), VerticalScroll)
        await container.remove_children()
        cards = [MessageCard(message) for message in result[q.JSON_MESSAGES]]
        if cards:
            await container.mount(*cards)
        else:
            await container.mount(Static("No messages match these filters.", markup=False))
        if latest:
            container.scroll_end(animate=False)
        else:
            container.scroll_home(animate=False)
        count = len(result[q.JSON_MESSAGES])
        first = self.message_offset + 1 if count else 0
        self.query_one(selector(t.ID_MESSAGE_STATUS), Static).update(
            f"Messages {first}–{self.message_offset + count} of {result[q.JSON_TOTAL_MESSAGES]}"
        )
        self.query_one(selector(t.ID_MESSAGE_PREVIOUS), Button).disabled = self.message_offset == 0
        self.query_one(selector(t.ID_MESSAGE_NEXT), Button).disabled = not result[q.JSON_HAS_MORE]
        self.query_one(selector(t.ID_MESSAGE_LATEST), Button).disabled = not result[q.JSON_HAS_MORE]
        self.update_output()
        return True

    def update_output(self) -> None:
        if not self._views_ready:
            return
        source = self.query_one(selector(t.ID_OUTPUT_SOURCE), Select).value
        output_format = self.query_one(selector(t.ID_OUTPUT_FORMAT), Select).value
        payload = self.channel_result if source == t.SOURCE_CHANNELS else self.conversation_result
        if payload is None:
            content = t.OUTPUT_EMPTY_MESSAGE
        elif output_format == q.FORMAT_JSON:
            content = json_output(payload)
        elif source == t.SOURCE_CHANNELS:
            content = render_channels(payload)
        else:
            content = render_conversation(payload, width=t.MESSAGE_PREVIEW_WIDTH)
        self.query_one(selector(t.ID_OUTPUT_PREVIEW), TextArea).load_text(content)
        self.query_one(selector(t.ID_SAVE_OUTPUT), Button).disabled = payload is None

    @on(Input.Changed, selector(t.ID_CHANNEL_SEARCH))
    def channel_search_changed(self) -> None:
        if not self._views_ready:
            return
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = self.set_timer(t.SEARCH_DELAY_SECONDS, self._search_channels)

    async def _search_channels(self) -> None:
        self.channel_page = q.DEFAULT_PAGE
        await self.load_channels()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == t.ID_CHANNEL_SEARCH:
            if self._search_timer is not None:
                self._search_timer.stop()
            await self._search_channels()
        elif event.input.id == t.ID_OUTPUT_PATH:
            self.action_save_output()
        elif event.input.id in (t.ID_CONVERSATION_SEARCH, t.ID_SINCE, t.ID_UNTIL):
            await self.load_conversation(reset=True)

    async def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if self._views_ready and event.checkbox.id in (t.ID_CHANNEL_REGEX, t.ID_INCLUDE_MESSAGES):
            await self._search_channels()

    def on_select_changed(self) -> None:
        self.update_output()

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_channel_id = str(event.row_key.value)
        await self.load_conversation(latest=True)
        self.query_one(selector(t.ID_TABS), TabbedContent).active = t.ID_CONVERSATION_TAB
        self.query_one(selector(t.ID_MESSAGE_LIST), VerticalScroll).focus()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        identifier = event.button.id
        if identifier in (t.ID_CHANNEL_PREVIOUS, t.ID_CHANNEL_NEXT):
            self.channel_page += -1 if identifier == t.ID_CHANNEL_PREVIOUS else 1
            await self.load_channels()
        elif identifier in (t.ID_MESSAGE_PREVIOUS, t.ID_MESSAGE_NEXT):
            change = (
                -t.MESSAGE_PAGE_SIZE if identifier == t.ID_MESSAGE_PREVIOUS else t.MESSAGE_PAGE_SIZE
            )
            self.message_offset = max(q.DEFAULT_OFFSET, self.message_offset + change)
            await self.load_conversation()
        elif identifier == t.ID_MESSAGE_LATEST:
            await self.load_conversation(latest=True)
        elif identifier == t.ID_APPLY_FILTERS:
            await self.load_conversation(reset=True)
        elif identifier == t.ID_RESET_FILTERS:
            for field in (t.ID_CONVERSATION_SEARCH, t.ID_SINCE, t.ID_UNTIL):
                self.query_one(selector(field), Input).value = ""
            for field in (t.ID_CONVERSATION_REGEX, t.ID_INCLUDE_REVISIONS):
                self.query_one(selector(field), Checkbox).value = False
            await self.load_conversation(latest=True)
        elif identifier == t.ID_SAVE_OUTPUT:
            self.action_save_output()

    def action_save_output(self) -> None:
        source = self.query_one(selector(t.ID_OUTPUT_SOURCE), Select).value
        payload = self.channel_result if source == t.SOURCE_CHANNELS else self.conversation_result
        if payload is None:
            self._status("No result is available to save.", error=True)
            return
        value = self._value(t.ID_OUTPUT_PATH)
        if not value:
            self._status("Enter a file path to save the selected output.", error=True)
            return
        try:
            path = save_output(
                Path(value), self.query_one(selector(t.ID_OUTPUT_PREVIEW), TextArea).text
            )
        except OSError as error:
            self._status(f"Output was not saved: {error.strerror}.", error=True)
        else:
            self._status(f"Saved {path}")

    def action_focus_search(self) -> None:
        self.query_one(selector(t.ID_TABS), TabbedContent).active = t.ID_CHANNEL_TAB
        self.query_one(selector(t.ID_CHANNEL_SEARCH), Input).focus()

    def action_focus_channels(self) -> None:
        self.query_one(selector(t.ID_TABS), TabbedContent).active = t.ID_CHANNEL_TAB
        self.query_one(selector(t.ID_CHANNEL_TABLE), DataTable).focus()

    async def action_refresh_data(self) -> None:
        await self.load_channels()

    def action_show_output(self) -> None:
        self.query_one(selector(t.ID_TABS), TabbedContent).active = t.ID_OUTPUT_TAB
        self.query_one(selector(t.ID_OUTPUT_PREVIEW), TextArea).focus()

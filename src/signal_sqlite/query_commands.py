"""Click adapters and aliases for read-only export queries."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from signal_sqlite import BackupError
from signal_sqlite import query_constants as q
from signal_sqlite.query import open_store, parse_date_bound
from signal_sqlite.render import json_output, render_channels, render_conversation


@dataclass(frozen=True)
class CLIOptions:
    output_format: str = q.FORMAT_TEXT
    database: Path = q.DEFAULT_DATABASE


def _emit(payload: dict[str, Any], options: CLIOptions, *, conversation: bool = False) -> None:
    if options.output_format == q.FORMAT_JSON:
        click.echo(json_output(payload))
    elif conversation:
        context = click.get_current_context()
        width = (
            context.terminal_width
            or shutil.get_terminal_size(
                (q.DEFAULT_TERMINAL_COLUMNS, q.DEFAULT_TERMINAL_LINES)
            ).columns
        )
        click.echo(render_conversation(payload, width=width))
    else:
        click.echo(render_channels(payload))


def _channel_query(
    options: CLIOptions,
    page: int,
    page_size: int,
    *,
    query: str | None = None,
    regex: bool = False,
    include_messages: bool = False,
) -> None:
    try:
        with open_store(options.database) as store:
            result = store.channel_page(
                page=page,
                page_size=page_size,
                query=query,
                regex=regex,
                include_messages=include_messages,
            )
        _emit(result, options)
    except BackupError as error:
        raise click.ClickException(str(error)) from None
    except OSError as error:
        raise click.ClickException(f"Database could not be read: {error.strerror}.") from None


def paging_options(function):
    function = click.option(
        q.OPTION_PAGE_SIZE,
        type=click.IntRange(min=q.MIN_PAGE_SIZE, max=q.MAX_PAGE_SIZE),
        default=q.DEFAULT_PAGE_SIZE,
        show_default=True,
        help="Channels per page.",
    )(function)
    return click.option(
        q.OPTION_PAGE,
        type=click.IntRange(min=q.MIN_PAGE_NUMBER),
        default=q.DEFAULT_PAGE,
        show_default=True,
        help="One-based page number.",
    )(function)


def regex_option(function):
    return click.option(
        q.OPTION_REGEX,
        q.OPTION_EXPRESSION,
        q.SHORT_OPTION_REGEX,
        is_flag=True,
        help="Interpret search text as a case-insensitive regular expression.",
    )(function)


@click.group(name=q.COMMAND_CHANNELS)
def channels() -> None:
    """List or search group and direct-message channels (alias: ch)."""


@channels.command(name=q.COMMAND_LIST)
@paging_options
@click.pass_obj
def list_channels(options: CLIOptions, page: int, page_size: int) -> None:
    """List all channels, newest activity first (alias: all)."""
    _channel_query(options, page, page_size)


@channels.command(name=q.COMMAND_SEARCH)
@paging_options
@regex_option
@click.option(
    q.OPTION_INCLUDE_MESSAGES,
    is_flag=True,
    help="Also search message bodies, quotes, captions, filenames, and revisions.",
)
@click.argument(q.PARAM_QUERY)
@click.pass_obj
def search_channels(
    options: CLIOptions,
    page: int,
    page_size: int,
    regex: bool,
    include_messages: bool,
    query: str,
) -> None:
    """Match participant names or group names (alias: match)."""
    _channel_query(
        options, page, page_size, query=query, regex=regex, include_messages=include_messages
    )


@click.group(name=q.COMMAND_CONVERSATIONS)
def conversations() -> None:
    """Read messages for a channel (aliases: convo, co)."""


@conversations.command(name=q.COMMAND_GET)
@click.argument(q.PARAM_CHANNEL_ID)
@click.option(
    q.OPTION_OFFSET, type=click.IntRange(min=0), default=q.DEFAULT_OFFSET, show_default=True
)
@click.option(q.OPTION_LIMIT, type=click.IntRange(min=0), help="Maximum messages; default is all.")
@click.option(
    q.OPTION_SINCE, help="Inclusive ISO 8601 date/time or epoch milliseconds; naive dates use UTC."
)
@click.option(
    q.OPTION_UNTIL, help="Inclusive date/time; a date alone includes that entire UTC day."
)
@click.option(q.OPTION_SEARCH, q.PARAM_QUERY, help="Filter messages by text (case-insensitive).")
@regex_option
@click.option(q.OPTION_INCLUDE_REVISIONS, is_flag=True, help="Include earlier message revisions.")
@click.pass_obj
def get_conversation(
    options: CLIOptions,
    channel_id: str,
    offset: int,
    limit: int | None,
    since: str | None,
    until: str | None,
    query: str | None,
    regex: bool,
    include_revisions: bool,
) -> None:
    """Read CHANNEL_ID chronologically, optionally selecting a subsection (alias: g)."""
    if regex and query is None:
        raise click.UsageError("--regex requires --search TEXT.")
    try:
        start = parse_date_bound(since)
        end = parse_date_bound(until, end=True)
        with open_store(options.database) as store:
            result = store.conversation(
                channel_id,
                offset=offset,
                limit=limit,
                since=start,
                until=end,
                query=query,
                regex=regex,
                include_revisions=include_revisions,
            )
        _emit(result, options, conversation=True)
    except BackupError as error:
        raise click.ClickException(str(error)) from None
    except OSError as error:
        raise click.ClickException(f"Database could not be read: {error.strerror}.") from None


def register_query_commands(root: click.Group) -> None:
    channels.add_command(list_channels, name=q.ALIAS_LIST)
    channels.add_command(search_channels, name=q.ALIAS_SEARCH)
    conversations.add_command(get_conversation, name=q.ALIAS_GET)
    root.add_command(channels)
    root.add_command(channels, name=q.ALIAS_CHANNELS)
    root.add_command(conversations)
    root.add_command(conversations, name=q.ALIAS_CONVERSATIONS)
    root.add_command(conversations, name=q.SHORT_ALIAS_CONVERSATIONS)

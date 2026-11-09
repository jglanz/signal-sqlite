"""Click command-line interface. Recovery keys never appear in command arguments."""

from pathlib import Path

import click

from signal_sqlite import BackupError, __version__
from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite import tui_constants as t
from signal_sqlite.backup import extract_backup, select_snapshot
from signal_sqlite.crypto import normalize_key, read_key_file
from signal_sqlite.query_commands import CLIOptions, register_query_commands
from signal_sqlite.render import json_output

PROMPT_RECOVERY_KEY = "Signal recovery key (hidden)"


@click.group(context_settings=c.CLI_CONTEXT_SETTINGS)
@click.version_option(version=__version__, prog_name=c.PROJECT_NAME)
@click.option(
    q.SHORT_OPTION_FORMAT,
    q.OPTION_FORMAT,
    q.PARAM_FORMAT,
    type=click.Choice((q.FORMAT_TEXT, q.FORMAT_JSON)),
    default=q.FORMAT_TEXT,
    show_default=True,
    help="Output format for extraction and query commands.",
)
@click.option(
    q.SHORT_OPTION_DATABASE,
    q.OPTION_DATABASE,
    type=click.Path(path_type=Path),
    default=q.DEFAULT_DATABASE,
    show_default=True,
    help="Existing SQLite database or export directory for query commands.",
)
@click.pass_context
def cli(context: click.Context, output_format: str, database: Path) -> None:
    """Extract Signal backups and query channels and conversations."""
    context.obj = CLIOptions(output_format=output_format, database=database)


@cli.command(name=c.COMMAND_EXTRACT_BACKUP)
@click.option(
    c.OPTION_SKIP_ATTACHMENTS,
    is_flag=True,
    help="Keep attachment metadata without decrypting attachment files.",
)
@click.option(
    c.OPTION_KEY_FILE,
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Owner-only UTF-8 file containing the recovery key; spaces/line breaks are accepted.",
)
@click.argument(c.INPUT_ARGUMENT, type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.argument(c.OUTPUT_ARGUMENT, type=click.Path(path_type=Path))
@click.pass_obj
def extract_command(
    options: CLIOptions,
    skip_attachments: bool,
    key_file: Path | None,
    input_path: Path,
    output_path: Path,
) -> None:
    """Extract INPUT_PATH into OUTPUT_PATH, creating any missing parent directories."""
    try:
        snapshot = select_snapshot(input_path)
        click.echo(f"Snapshot: {snapshot}", err=options.output_format == q.FORMAT_JSON)
        if key_file is not None:
            key = read_key_file(key_file)
        else:
            import sys

            if not sys.stdin.isatty():
                raise BackupError(
                    "Use --key-file PATH when running without an interactive terminal."
                )
            key = normalize_key(click.prompt(PROMPT_RECOVERY_KEY, hide_input=True))
        result = extract_backup(snapshot, output_path, key, skip_attachments=skip_attachments)
    except BackupError as error:
        raise click.ClickException(str(error)) from None
    except OSError as error:
        raise click.ClickException(f"Filesystem operation failed: {error.strerror}.") from None
    counts = result.manifest[c.JSON_KEY_COUNTS]
    if options.output_format == q.FORMAT_JSON:
        click.echo(
            json_output(
                {
                    q.JSON_DATABASE: str(result.output / c.DATABASE_NAME),
                    q.JSON_SNAPSHOT: str(result.snapshot),
                    **result.manifest,
                }
            )
        )
    else:
        click.echo(f"Database: {result.output / c.DATABASE_NAME}")
        click.echo(
            f"Exported {counts[c.COUNT_MESSAGES]} messages, "
            f"{counts[c.COUNT_REVISIONS]} revisions, "
            f"{counts[c.COUNT_ATTACHMENTS]} attachment references."
        )
    for warning in result.manifest[c.JSON_KEY_WARNINGS]:
        click.echo(f"Warning: {warning}", err=True)
    if not result.complete:
        click.echo("Export is usable but incomplete; inspect manifest.json for details.", err=True)
        raise click.exceptions.Exit(c.EXIT_INCOMPLETE)


register_query_commands(cli)


@cli.command(name=t.COMMAND_TUI)
@click.pass_obj
def tui_command(options: CLIOptions) -> None:
    """Browse channels, conversations, and text/JSON output in a Textual TUI."""
    import sys

    from signal_sqlite.query import open_store
    from signal_sqlite.tui import SignalSQLiteApp

    try:
        with open_store(options.database) as store:
            database = store.path
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise BackupError("The TUI requires an interactive terminal.")
        SignalSQLiteApp(database, output_format=options.output_format).run()
    except BackupError as error:
        raise click.ClickException(str(error)) from None
    except OSError as error:
        raise click.ClickException(f"The TUI could not start: {error.strerror}.") from None

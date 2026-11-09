import stat

import pytest
from click.testing import CliRunner
from conftest import TEST_DISPLAY_KEY, TEST_KEY

from signal_sqlite import constants as c
from signal_sqlite.cli import cli

CLI_USAGE_EXIT = 2
NESTED_OUTPUT_PARTS = ("missing", "middle", "export")
EXISTING_FILENAME = "keep.txt"
WRONG_KEY = "z" * c.KEY_LENGTH
BAD_KEY_BYTES = b"\xff\xfe"
KEY_FILE_LIMIT_ATTRIBUTE = "MAX_KEY_FILE_SIZE_BYTES"
TEST_SMALL_KEY_LIMIT = 2


def test_help_and_version():
    runner = CliRunner()
    assert runner.invoke(cli, [c.OPTION_HELP]).exit_code == 0
    assert runner.invoke(cli, [c.OPTION_VERSION]).exit_code == 0
    help_result = runner.invoke(cli, [c.COMMAND_EXTRACT_BACKUP, c.OPTION_HELP])
    assert c.OPTION_KEY_FILE in help_result.output
    assert c.OPTION_SKIP_ATTACHMENTS in help_result.output


@pytest.mark.parametrize("arguments", [[], [c.COMMAND_EXTRACT_BACKUP]])
def test_required_command_and_positionals(arguments):
    assert CliRunner().invoke(cli, arguments).exit_code == CLI_USAGE_EXIT


def test_noninteractive_invocation_requires_key_file(backup):
    result = CliRunner().invoke(
        cli, [c.COMMAND_EXTRACT_BACKUP, str(backup.root), str(backup.output)]
    )
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "--key-file PATH" in result.output


def test_creates_missing_output_and_all_parents(backup, invoke_extract):
    output = backup.output.joinpath(*NESTED_OUTPUT_PARTS)
    result = invoke_extract(backup, output=output)
    assert result.exit_code == 0, result.output
    assert (output / c.DATABASE_NAME).is_file()
    current = output
    while current != backup.output.parent:
        assert stat.S_IMODE(current.stat().st_mode) == c.DIRECTORY_MODE
        current = current.parent
    for file in output.rglob(c.ALL_ENTRIES_GLOB):
        expected = c.DIRECTORY_MODE if file.is_dir() else c.FILE_MODE
        assert stat.S_IMODE(file.stat().st_mode) == expected


def test_existing_empty_output_is_accepted(backup, invoke_extract):
    backup.output.mkdir()
    assert invoke_extract(backup).exit_code == 0


def test_displayed_key_file_extracts_without_changing_key_file(backup, invoke_extract):
    backup.key_path.write_text(TEST_DISPLAY_KEY, encoding=c.UTF8)
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    assert backup.key_path.read_text(encoding=c.UTF8) == TEST_DISPLAY_KEY
    assert TEST_DISPLAY_KEY not in result.output
    assert TEST_KEY not in result.output


def test_nonempty_output_is_preserved(backup, invoke_extract):
    backup.output.mkdir()
    sentinel = backup.output / EXISTING_FILENAME
    sentinel.write_text("Keep this content")
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert sentinel.read_text() == "Keep this content"
    assert not (backup.output / c.DATABASE_NAME).exists()


def test_output_file_is_preserved(backup, invoke_extract):
    backup.output.write_text("Keep this file")
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert backup.output.read_text() == "Keep this file"


def test_output_cannot_be_inside_backup(backup, invoke_extract):
    result = invoke_extract(backup, output=backup.root / c.ATTACHMENTS_NAME)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "outside the source" in result.output


def test_wrong_key_does_not_publish_or_echo_secret(backup, invoke_extract):
    backup.key_path.write_text(WRONG_KEY)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "Authentication failed" in result.output
    assert WRONG_KEY not in result.output
    assert TEST_KEY not in result.output
    assert not list(backup.output.iterdir())
    assert not list(backup.output.parent.glob(c.STAGING_PREFIX + c.ALL_ENTRIES_GLOB))
    assert backup.key_path.read_text() == WRONG_KEY


def test_non_utf8_key_has_clean_error(backup, invoke_extract):
    backup.key_path.write_bytes(BAD_KEY_BYTES)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "UTF-8" in result.output


def test_oversized_key_file(backup, invoke_extract, monkeypatch):
    monkeypatch.setattr(c, KEY_FILE_LIMIT_ATTRIBUTE, TEST_SMALL_KEY_LIMIT)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "too large" in result.output

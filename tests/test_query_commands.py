import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import (
    CHAT_ID,
    QUERY_ALICE_NAME,
    QUERY_BOB_NAME,
    QUERY_CHANGE_OFFSET_MS,
    QUERY_EMPTY_CHAT_ID,
    QUERY_GROUP_CHAT_ID,
    QUERY_GROUP_NAME,
    QUERY_OWN_TEXT,
    QUERY_RECEIVED_OFFSET_MS,
    TEST_KEY,
    TEST_REVISION,
    TEST_TIMESTAMP_MS,
)

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite import query_constants as q
from signal_sqlite.cli import cli
from signal_sqlite.query import open_store, parse_date_bound

QUERY_CHANNEL_COUNT = 3
CLI_USAGE_EXIT = 2
SINGLE_PAGE_SIZE = "1"
INVALID_REGEX = "["
MISSING_CHANNEL_ID = "99999"
MISSING_DATABASE_NAME = "missing.sqlite3"
BAD_DATABASE_NAME = "bad.sqlite3"
QUERY_ISO_DATE = "2024-07-03"
QUERY_ISO_TIME = "2024-07-03T09:46:40+00:00"
WRITE_ATTEMPT_SQL = "CREATE TABLE should_not_exist (id INTEGER)"
BEYOND_LAST_PAGE = "9"
LIMIT_ONE = "1"
OFFSET_ONE = "1"
LIMIT_ZERO = "0"
INVALID_ARGUMENT_VALUES = (
    (q.OPTION_PAGE, "0"),
    (q.OPTION_PAGE_SIZE, "0"),
    (q.OPTION_PAGE_SIZE, str(q.MAX_PAGE_SIZE + 1)),
)


@pytest.mark.parametrize("group", [q.COMMAND_CHANNELS, q.ALIAS_CHANNELS])
@pytest.mark.parametrize("command", [q.COMMAND_LIST, q.ALIAS_LIST])
def test_list_aliases_and_channel_metadata(query_export, query_payload, group, command):
    result = query_payload(query_export, group, command)
    assert result[q.JSON_TOTAL] == QUERY_CHANNEL_COUNT
    assert [channel[q.JSON_ID] for channel in result[q.JSON_CHANNELS]] == [
        str(QUERY_GROUP_CHAT_ID),
        str(CHAT_ID),
        str(QUERY_EMPTY_CHAT_ID),
    ]
    group_channel, direct_channel, empty_channel = result[q.JSON_CHANNELS]
    assert group_channel[q.JSON_GROUP_NAME] == QUERY_GROUP_NAME
    names = [person[q.JSON_NAME] for person in group_channel[q.JSON_PARTICIPANTS]]
    assert QUERY_ALICE_NAME in names
    assert QUERY_BOB_NAME in names
    assert group_channel[q.JSON_LAST_ACTIVITY_MS] == TEST_TIMESTAMP_MS + QUERY_CHANGE_OFFSET_MS
    assert direct_channel[q.JSON_LAST_ACTIVITY_MS] == TEST_TIMESTAMP_MS + QUERY_RECEIVED_OFFSET_MS
    assert direct_channel[q.JSON_GROUP_NAME] is None
    assert empty_channel[q.JSON_LAST_ACTIVITY_MS] is None
    assert empty_channel[q.JSON_LAST_ACTIVITY_UTC] is None


def test_channel_paging_is_stable_and_reports_totals(query_export, query_payload):
    seen = []
    for page in range(q.DEFAULT_PAGE, QUERY_CHANNEL_COUNT + 1):
        result = query_payload(
            query_export,
            q.COMMAND_CHANNELS,
            q.COMMAND_LIST,
            q.OPTION_PAGE,
            str(page),
            q.OPTION_PAGE_SIZE,
            SINGLE_PAGE_SIZE,
        )
        assert result[q.JSON_TOTAL_PAGES] == QUERY_CHANNEL_COUNT
        seen.extend(channel[q.JSON_ID] for channel in result[q.JSON_CHANNELS])
    assert seen == [str(QUERY_GROUP_CHAT_ID), str(CHAT_ID), str(QUERY_EMPTY_CHAT_ID)]
    beyond = query_payload(
        query_export,
        q.COMMAND_CHANNELS,
        q.COMMAND_LIST,
        q.OPTION_PAGE,
        BEYOND_LAST_PAGE,
        q.OPTION_PAGE_SIZE,
        SINGLE_PAGE_SIZE,
    )
    assert beyond[q.JSON_CHANNELS] == []
    assert beyond[q.JSON_TOTAL] == QUERY_CHANNEL_COUNT


@pytest.mark.parametrize("option,value", INVALID_ARGUMENT_VALUES)
def test_invalid_paging_is_usage_error(query_export, invoke_query, option, value):
    result = invoke_query(query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST, option, value)
    assert result.exit_code == CLI_USAGE_EXIT


@pytest.mark.parametrize("group", [q.COMMAND_CHANNELS, q.ALIAS_CHANNELS])
@pytest.mark.parametrize("command", [q.COMMAND_SEARCH, q.ALIAS_SEARCH])
def test_participant_name_alias_search(query_export, query_payload, group, command):
    result = query_payload(query_export, group, command, "ALICE")
    assert {channel[q.JSON_ID] for channel in result[q.JSON_CHANNELS]} == {
        str(CHAT_ID),
        str(QUERY_GROUP_CHAT_ID),
    }


@pytest.mark.parametrize("option", [q.OPTION_REGEX, q.OPTION_EXPRESSION, q.SHORT_OPTION_REGEX])
def test_group_name_regex_and_alias_flags(query_export, query_payload, option):
    result = query_payload(
        query_export, q.COMMAND_CHANNELS, q.ALIAS_SEARCH, option, "^launch.*team$"
    )
    assert [channel[q.JSON_ID] for channel in result[q.JSON_CHANNELS]] == [str(QUERY_GROUP_CHAT_ID)]


def test_regex_is_opt_in_and_invalid_expression_fails(query_export, query_payload, invoke_query):
    result = query_payload(query_export, q.COMMAND_CHANNELS, q.COMMAND_SEARCH, "Launch.*")
    assert result[q.JSON_CHANNELS] == []
    invalid = invoke_query(
        query_export, q.COMMAND_CHANNELS, q.COMMAND_SEARCH, q.OPTION_REGEX, INVALID_REGEX
    )
    assert invalid.exit_code == c.EXIT_INCOMPLETE
    assert "Invalid regular expression" in invalid.stderr


@pytest.mark.parametrize(
    "term", ["announcement", "résumé", "handout", "report.txt", "Earlier draft"]
)
def test_optional_message_matching(query_export, query_payload, term):
    names_only = query_payload(query_export, q.COMMAND_CHANNELS, q.COMMAND_SEARCH, term)
    assert names_only[q.JSON_TOTAL] == 0
    messages = query_payload(
        query_export, q.COMMAND_CHANNELS, q.COMMAND_SEARCH, q.OPTION_INCLUDE_MESSAGES, term
    )
    assert messages[q.JSON_TOTAL] == 1


def test_message_search_with_regex_and_pagination(query_export, query_payload):
    result = query_payload(
        query_export,
        q.ALIAS_CHANNELS,
        q.ALIAS_SEARCH,
        q.SHORT_OPTION_REGEX,
        q.OPTION_INCLUDE_MESSAGES,
        q.OPTION_PAGE_SIZE,
        SINGLE_PAGE_SIZE,
        "announcement|résumé",
    )
    assert result[q.JSON_TOTAL] == 2
    assert len(result[q.JSON_CHANNELS]) == 1
    assert result[q.JSON_TOTAL_PAGES] == 2


@pytest.mark.parametrize(
    "group", [q.COMMAND_CONVERSATIONS, q.ALIAS_CONVERSATIONS, q.SHORT_ALIAS_CONVERSATIONS]
)
@pytest.mark.parametrize("command", [q.COMMAND_GET, q.ALIAS_GET])
def test_conversation_aliases_and_attachment_paths(query_export, query_payload, group, command):
    result = query_payload(query_export, group, command, str(CHAT_ID))
    assert result[q.JSON_CHANNEL][q.JSON_ID] == str(CHAT_ID)
    assert len(result[q.JSON_MESSAGES]) == 2
    assert result[q.JSON_MESSAGES][0][q.JSON_AUTHORED_BY_ME] is False
    assert result[q.JSON_MESSAGES][1][q.JSON_AUTHORED_BY_ME] is True
    assert result[q.JSON_MESSAGES][1][q.JSON_BODY] == QUERY_OWN_TEXT
    nested = [
        attachment
        for message in result[q.JSON_MESSAGES]
        for attachment in message[q.JSON_ATTACHMENTS]
    ]
    assert nested == result[q.JSON_ATTACHMENTS]
    for attachment in nested:
        if attachment[q.JSON_STATUS] == c.STATUS_EXPORTED:
            assert not Path(attachment[q.JSON_RELATIVE_PATH]).is_absolute()
            absolute = Path(attachment[q.JSON_ABSOLUTE_PATH])
            assert absolute == query_export.output / attachment[q.JSON_RELATIVE_PATH]
            assert absolute.is_file()
        else:
            assert attachment[q.JSON_STATUS] == c.STATUS_UNAVAILABLE
            assert attachment[q.JSON_RELATIVE_PATH] is None
            assert attachment[q.JSON_ABSOLUTE_PATH] is None


def test_conversation_range_limits_attachments_to_selected_messages(query_export, query_payload):
    result = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_OFFSET,
        OFFSET_ONE,
        q.OPTION_LIMIT,
        LIMIT_ONE,
    )
    assert result[q.JSON_TOTAL_MESSAGES] == 2
    assert len(result[q.JSON_MESSAGES]) == 1
    assert result[q.JSON_MESSAGES][0][q.JSON_BODY] == QUERY_OWN_TEXT
    assert all(
        attachment[q.JSON_MESSAGE_ID] == result[q.JSON_MESSAGES][0][q.JSON_ID]
        for attachment in result[q.JSON_ATTACHMENTS]
    )
    assert result[q.JSON_HAS_MORE] is False
    empty = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_LIMIT,
        LIMIT_ZERO,
    )
    assert empty[q.JSON_MESSAGES] == empty[q.JSON_ATTACHMENTS] == []
    assert empty[q.JSON_HAS_MORE] is True


def test_message_revisions_and_search(query_export, query_payload):
    current = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_SEARCH,
        TEST_REVISION,
    )
    assert current[q.JSON_MESSAGES] == []
    revisions = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_INCLUDE_REVISIONS,
        q.OPTION_SEARCH,
        TEST_REVISION,
    )
    assert len(revisions[q.JSON_MESSAGES]) == 1
    assert revisions[q.JSON_MESSAGES][0][q.JSON_PARENT_MESSAGE_ID] is not None
    regex = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_SEARCH,
        "myself|release",
        q.OPTION_REGEX,
    )
    assert len(regex[q.JSON_MESSAGES]) == 2


def test_date_filters_and_date_only_inclusivity(query_export, query_payload):
    exact = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_SINCE,
        QUERY_ISO_TIME,
        q.OPTION_UNTIL,
        str(TEST_TIMESTAMP_MS),
    )
    assert len(exact[q.JSON_MESSAGES]) == 1
    full_day = query_payload(
        query_export,
        q.COMMAND_CONVERSATIONS,
        q.COMMAND_GET,
        str(CHAT_ID),
        q.OPTION_SINCE,
        QUERY_ISO_DATE,
        q.OPTION_UNTIL,
        QUERY_ISO_DATE,
    )
    assert len(full_day[q.JSON_MESSAGES]) == 2
    assert parse_date_bound(QUERY_ISO_TIME) == TEST_TIMESTAMP_MS


@pytest.mark.parametrize(
    "options",
    [
        (q.OPTION_SINCE, "not-a-date"),
        (q.OPTION_SINCE, "2025-01-01", q.OPTION_UNTIL, "2024-01-01"),
        (q.OPTION_SEARCH, INVALID_REGEX, q.OPTION_REGEX),
    ],
)
def test_invalid_conversation_filters_fail(query_export, invoke_query, options):
    result = invoke_query(
        query_export, q.COMMAND_CONVERSATIONS, q.COMMAND_GET, str(CHAT_ID), *options
    )
    assert result.exit_code == c.EXIT_INCOMPLETE


def test_regex_requires_a_search_and_missing_id_is_clear(query_export, invoke_query):
    result = invoke_query(
        query_export, q.COMMAND_CONVERSATIONS, q.COMMAND_GET, str(CHAT_ID), q.OPTION_REGEX
    )
    assert result.exit_code == CLI_USAGE_EXIT
    missing = invoke_query(query_export, q.COMMAND_CONVERSATIONS, q.COMMAND_GET, MISSING_CHANNEL_ID)
    assert missing.exit_code == c.EXIT_INCOMPLETE
    assert "was not found" in missing.stderr


def test_default_database_and_export_directory_selection(query_export, invoke_query, monkeypatch):
    monkeypatch.chdir(query_export.output.parent)
    query_export.output.rename(q.OUTPUT_DIRECTORY)
    result = CliRunner().invoke(
        cli, [q.SHORT_OPTION_FORMAT, q.FORMAT_JSON, q.COMMAND_CHANNELS, q.COMMAND_LIST]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[q.JSON_TOTAL] == QUERY_CHANNEL_COUNT
    directory = invoke_query(
        query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST, database=Path(q.OUTPUT_DIRECTORY)
    )
    assert directory.exit_code == 0, directory.output


def test_queries_are_read_only(query_export, query_payload):
    path = query_export.output / c.DATABASE_NAME
    before = hashlib.sha256(path.read_bytes()).digest()
    query_payload(query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST)
    query_payload(
        query_export, q.COMMAND_CHANNELS, q.COMMAND_SEARCH, q.OPTION_INCLUDE_MESSAGES, "release"
    )
    query_payload(query_export, q.COMMAND_CONVERSATIONS, q.COMMAND_GET, str(CHAT_ID))
    assert hashlib.sha256(path.read_bytes()).digest() == before
    with pytest.raises(BackupError, match="Cannot query"), open_store(path) as store:
        store.connection.execute(WRITE_ATTEMPT_SQL)


def test_missing_and_invalid_database_do_not_create_files(query_export, invoke_query):
    missing = query_export.output / MISSING_DATABASE_NAME
    result = invoke_query(query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST, database=missing)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "Database not found" in result.stderr
    assert not missing.exists()
    invalid = query_export.output / BAD_DATABASE_NAME
    invalid.write_text("This is not a SQLite database", encoding=c.UTF8)
    result = invoke_query(query_export, q.COMMAND_CHANNELS, q.COMMAND_LIST, database=invalid)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "Cannot query" in result.stderr


def test_empty_channel_still_has_metadata(query_export, query_payload):
    result = query_payload(
        query_export, q.COMMAND_CONVERSATIONS, q.COMMAND_GET, str(QUERY_EMPTY_CHAT_ID)
    )
    assert result[q.JSON_CHANNEL][q.JSON_PARTICIPANTS]
    assert result[q.JSON_MESSAGES] == result[q.JSON_ATTACHMENTS] == []


def test_extraction_obeys_global_json_format(backup):
    result = CliRunner().invoke(
        cli,
        [
            q.SHORT_OPTION_FORMAT,
            q.FORMAT_JSON,
            c.COMMAND_EXTRACT_BACKUP,
            c.OPTION_KEY_FILE,
            str(backup.key_path),
            str(backup.root),
            str(backup.output),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[c.JSON_KEY_COMPLETE] is True
    assert payload[q.JSON_DATABASE] == str(backup.output / c.DATABASE_NAME)
    assert TEST_KEY not in result.output

from conftest import (
    TEST_KEY,
    TEST_LONG_TEXT,
    TEST_QUOTE,
    TEST_REVISION,
    TEST_TEXT,
    TEST_TIMESTAMP_MS,
    TEST_UNKNOWN_FRAME,
)

from signal_sqlite import constants as c

QUERY_MESSAGES = """
SELECT body, quoted_text, date_sent_ms, author_name, conversation_name
FROM message_details WHERE parent_message_id IS NULL
"""
QUERY_REVISIONS = "SELECT body, parent_message_id FROM messages WHERE parent_message_id IS NOT NULL"
QUERY_SEARCH = "SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?"
QUERY_REACTIONS = "SELECT emoji, author_id FROM reactions"
QUERY_RAW_RECORDS = "SELECT raw_protobuf FROM frames ORDER BY id"
QUERY_FK_CHECK = "PRAGMA foreign_key_check"
QUERY_INTEGRITY = "PRAGMA integrity_check"
QUERY_SCHEMA = "PRAGMA user_version"
INTEGRITY_OK = "ok"
SEARCH_TERMS = ("cafe", "résumé", "discussion", "handout", "report", "draft")
EXPECTED_REACTION = ("👍", "1")
EXPECTED_CONTACT_NAME = "Alice Example"
LONG_TEXT_SEARCH_TERM = "truncated"


def test_relational_export_and_search(backup, invoke_extract, query_database):
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    rows = query_database(backup, QUERY_MESSAGES)
    assert rows == [
        (TEST_TEXT, TEST_QUOTE, TEST_TIMESTAMP_MS, EXPECTED_CONTACT_NAME, EXPECTED_CONTACT_NAME)
    ]
    revisions = query_database(backup, QUERY_REVISIONS)
    assert revisions[0][0] == TEST_REVISION
    assert revisions[0][1] is not None
    for term in SEARCH_TERMS:
        assert query_database(backup, QUERY_SEARCH, (term,))
    assert query_database(backup, QUERY_REACTIONS) == [EXPECTED_REACTION]
    assert TEST_KEY not in result.output


def test_raw_records_are_byte_exact_including_unknown_fields(
    backup, invoke_extract, query_database
):
    assert invoke_extract(backup).exit_code == 0
    raw = [row[0] for row in query_database(backup, QUERY_RAW_RECORDS)]
    assert raw == backup.raw_records
    assert raw[-1] == TEST_UNKNOWN_FRAME


def test_database_integrity(backup, invoke_extract, query_database):
    assert invoke_extract(backup).exit_code == 0
    assert query_database(backup, QUERY_FK_CHECK) == []
    assert query_database(backup, QUERY_INTEGRITY) == [(INTEGRITY_OK,)]
    assert query_database(backup, QUERY_SCHEMA) == [(c.DATABASE_SCHEMA_VERSION,)]


def test_long_text_is_searchable(backup_factory, invoke_extract, query_database):
    backup = backup_factory(long_text=True)
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    assert query_database(backup, QUERY_MESSAGES)[0][0] == TEST_LONG_TEXT
    assert query_database(backup, QUERY_SEARCH, (LONG_TEXT_SEARCH_TERM,))

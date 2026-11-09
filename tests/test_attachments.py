import hashlib
import json

from signal_sqlite import constants as c

QUERY_ATTACHMENTS = "SELECT status, exported_path, error FROM attachments"
TAMPER_INDEX = c.AES_IV_LENGTH
TAMPER_XOR_MASK = 1
SOURCE_MISSING_LOCAL_KEY_FIELD = "localKey"
INCONSISTENT_SIZE_OFFSET = 1


def test_decrypted_attachment_is_verified_and_safely_named(backup, invoke_extract, query_database):
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    status, path, error = query_database(backup, QUERY_ATTACHMENTS)[0]
    exported = backup.output / path
    assert status == c.STATUS_EXPORTED
    assert error is None
    assert exported.resolve().is_relative_to(backup.output / c.ATTACHMENTS_NAME)
    assert exported.read_bytes() == backup.attachment_data
    assert (
        hashlib.sha256(exported.read_bytes()).digest()
        == hashlib.sha256(backup.attachment_data).digest()
    )


def test_skip_attachments_preserves_metadata(backup, invoke_extract, query_database):
    result = invoke_extract(backup, c.OPTION_SKIP_ATTACHMENTS)
    assert result.exit_code == 0, result.output
    assert query_database(backup, QUERY_ATTACHMENTS) == [(c.STATUS_SKIPPED, None, None)]
    assert not (backup.output / c.ATTACHMENTS_NAME).exists()


def test_missing_attachment_publishes_incomplete_database(backup, invoke_extract, query_database):
    backup.attachment_source.unlink()
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert query_database(backup, QUERY_ATTACHMENTS)[0][0] == c.STATUS_MISSING
    manifest = json.loads((backup.output / c.MANIFEST_NAME).read_text())
    assert manifest[c.JSON_KEY_COMPLETE] is False


def test_tampered_attachment_is_never_published(backup, invoke_extract, query_database):
    encrypted = bytearray(backup.attachment_source.read_bytes())
    encrypted[TAMPER_INDEX] ^= TAMPER_XOR_MASK
    backup.attachment_source.write_bytes(encrypted)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert query_database(backup, QUERY_ATTACHMENTS)[0][0] == c.STATUS_FAILED
    assert not list((backup.output / c.ATTACHMENTS_NAME).iterdir())


def test_empty_attachment_is_valid(backup_factory, invoke_extract, query_database):
    backup = backup_factory(empty_attachment=True)
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    status, relative, _ = query_database(backup, QUERY_ATTACHMENTS)[0]
    assert status == c.STATUS_EXPORTED
    assert (backup.output / relative).read_bytes() == b""


def test_remote_only_attachment_is_recorded_unavailable(backup, invoke_extract, query_database):
    pointer = backup.frames[-1].chatItem.standardMessage.attachments[0].pointer
    pointer.locatorInfo.ClearField(SOURCE_MISSING_LOCAL_KEY_FIELD)
    backup.rewrite()
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    assert query_database(backup, QUERY_ATTACHMENTS)[0][0] == c.STATUS_UNAVAILABLE


def test_shared_media_with_conflicting_size_is_reported(backup, invoke_extract, query_database):
    standard = backup.frames[-1].chatItem.standardMessage
    duplicate = standard.attachments.add()
    duplicate.CopyFrom(standard.attachments[0])
    duplicate.pointer.locatorInfo.size += INCONSISTENT_SIZE_OFFSET
    backup.rewrite()
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    statuses = [row[0] for row in query_database(backup, QUERY_ATTACHMENTS)]
    assert statuses == [c.STATUS_EXPORTED, c.STATUS_FAILED]

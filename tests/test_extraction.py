import hashlib
import json

from conftest import CONTACT_ID, TEST_KEY

from signal_sqlite import constants as c

TAMPER_INDEX = c.AES_IV_LENGTH
TAMPER_XOR_MASK = 1
UNSUPPORTED_VERSION = c.SUPPORTED_BACKUP_VERSION + 1
MISSING_RECIPIENT_ID = CONTACT_ID + 100
MALFORMED_PROTOBUF = b"\x08"
EXPECTED_FRAME_COUNT = 7
EXPECTED_MESSAGE_COUNT = 1
EXPECTED_REVISION_COUNT = 1
EXPECTED_ATTACHMENT_COUNT = 1


def test_manifest_provenance_and_unchanged_sources(backup, invoke_extract):
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in backup.root.rglob(c.ALL_ENTRIES_GLOB)
        if path.is_file()
    }
    result = invoke_extract(backup)
    assert result.exit_code == 0, result.output
    manifest = json.loads((backup.output / c.MANIFEST_NAME).read_text())
    assert manifest[c.JSON_KEY_COMPLETE] is True
    assert manifest[c.JSON_KEY_SOURCE] == str(backup.snapshot)
    for name, digest in manifest[c.JSON_KEY_SOURCE_HASHES].items():
        assert before[backup.snapshot / name] == digest
    assert all(
        hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items()
    )
    counts = manifest[c.JSON_KEY_COUNTS]
    assert counts[c.COUNT_FRAMES] == EXPECTED_FRAME_COUNT
    assert counts[c.COUNT_MESSAGES] == EXPECTED_MESSAGE_COUNT
    assert counts[c.COUNT_REVISIONS] == EXPECTED_REVISION_COUNT
    assert counts[c.COUNT_ATTACHMENTS] == EXPECTED_ATTACHMENT_COUNT
    assert TEST_KEY not in (backup.output / c.MANIFEST_NAME).read_text()
    assert backup.key_path.read_text() == TEST_KEY


def test_tampered_main_leaves_empty_output(backup, invoke_extract):
    path = backup.snapshot / c.MAIN_NAME
    encrypted = bytearray(path.read_bytes())
    encrypted[TAMPER_INDEX] ^= TAMPER_XOR_MASK
    path.write_bytes(encrypted)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "Authentication failed" in result.output
    assert not list(backup.output.iterdir())


def test_unsupported_backup_version_is_rejected(backup, invoke_extract):
    backup.header.version = UNSUPPORTED_VERSION
    backup.rewrite()
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert "Unsupported message backup version" in result.output
    assert not list(backup.output.iterdir())


def test_broken_reference_is_not_silently_dropped(backup, invoke_extract):
    backup.frames[-1].chatItem.authorId = MISSING_RECIPIENT_ID
    backup.rewrite()
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert not list(backup.output.iterdir())


def test_malformed_authenticated_payload_fails_cleanly(backup, invoke_extract):
    backup.rewrite(raw_plaintext=MALFORMED_PROTOBUF)
    result = invoke_extract(backup)
    assert result.exit_code == c.EXIT_INCOMPLETE
    assert not list(backup.output.iterdir())
    assert not list(backup.output.parent.glob(c.STAGING_PREFIX + c.ALL_ENTRIES_GLOB))

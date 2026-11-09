import io
import shutil

import pytest
from conftest import encode_record

from signal_sqlite import BackupError
from signal_sqlite import constants as c
from signal_sqlite.backup import iter_records, select_snapshot

NEWER_SNAPSHOT_NAME = "signal-backup-2026-11-07-18-24-39"
TRUNCATED_VARINT = b"\x80"
OVERFLOW_VARINT = b"\xff" * c.MAX_VARINT_BYTES
TRUNCATED_RECORD = b"\x05ab"
FRAME_SIZE_LIMIT_ATTRIBUTE = "MAX_FRAME_SIZE_BYTES"
TEST_MAX_FRAME_BYTES = 1


def test_root_and_snapshot_inputs(backup):
    assert select_snapshot(backup.root) == backup.snapshot
    assert select_snapshot(backup.snapshot) == backup.snapshot


def test_newest_snapshot_is_selected(backup):
    newer = backup.root / NEWER_SNAPSHOT_NAME
    shutil.copytree(backup.snapshot, newer)
    assert select_snapshot(backup.root) == newer


def test_incomplete_newest_snapshot_is_not_silently_skipped(backup):
    (backup.root / NEWER_SNAPSHOT_NAME).mkdir()
    with pytest.raises(BackupError, match="newest backup snapshot"):
        select_snapshot(backup.root)


def test_missing_snapshot(tmp_path):
    with pytest.raises(BackupError, match="No Signal"):
        select_snapshot(tmp_path)


@pytest.mark.parametrize("data", [TRUNCATED_VARINT, OVERFLOW_VARINT, TRUNCATED_RECORD])
def test_malformed_framing(data):
    with pytest.raises(BackupError):
        list(iter_records(io.BytesIO(data)))


def test_frame_size_limit(monkeypatch):
    monkeypatch.setattr(c, FRAME_SIZE_LIMIT_ATTRIBUTE, TEST_MAX_FRAME_BYTES)
    with pytest.raises(BackupError, match="maximum supported size"):
        list(iter_records(io.BytesIO(encode_record(b"too big"))))


def test_empty_frame_is_preserved():
    assert list(iter_records(io.BytesIO(encode_record(b"")))) == [b""]

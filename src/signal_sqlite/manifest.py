"""Non-secret provenance and extraction summary."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from signal_sqlite import __version__
from signal_sqlite import constants as c


def build_manifest(
    snapshot: Path,
    header: Any,
    source_hashes: dict[str, str],
    counts: dict[str, Any],
    warnings: list[str],
    *,
    skip_attachments: bool,
    complete: bool,
) -> dict[str, Any]:
    return {
        c.JSON_KEY_TOOL: c.PROJECT_NAME,
        c.JSON_KEY_TOOL_VERSION: __version__,
        c.JSON_KEY_SCHEMA_VERSION: c.DATABASE_SCHEMA_VERSION,
        c.JSON_KEY_SOURCE: str(snapshot),
        c.JSON_KEY_BACKUP_VERSION: header.version,
        c.JSON_KEY_BACKUP_TIME: header.backupTimeMs,
        c.JSON_KEY_APP_VERSION: header.currentAppVersion,
        c.JSON_KEY_SOURCE_HASHES: source_hashes,
        c.JSON_KEY_COUNTS: counts,
        c.JSON_KEY_COMPLETE: complete,
        c.JSON_KEY_SKIP_ATTACHMENTS: skip_attachments,
        c.JSON_KEY_CREATED: datetime.now(UTC).isoformat(),
        c.JSON_KEY_WARNINGS: warnings,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(manifest, indent=c.JSON_INDENT, ensure_ascii=False) + c.TEXT_SEPARATOR,
        encoding=c.UTF8,
    )
    path.chmod(c.FILE_MODE)

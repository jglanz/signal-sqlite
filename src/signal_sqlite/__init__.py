"""Extract, search, and browse local Signal backups with SQLite, a CLI, and a TUI."""

__version__ = "1.0.0"


class BackupError(Exception):
    """An expected extraction failure that can be reported without secret data."""

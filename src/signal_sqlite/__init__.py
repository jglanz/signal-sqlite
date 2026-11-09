"""Extract, search, and browse local Signal backups with SQLite, a CLI, and a TUI."""

__version__ = "0.1.1"


class BackupError(Exception):
    """An expected extraction failure that can be reported without secret data."""

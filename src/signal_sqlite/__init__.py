"""Local, authenticated Signal backup extraction."""

__version__ = "0.1.0"


class BackupError(Exception):
    """An expected extraction failure that can be reported without secret data."""

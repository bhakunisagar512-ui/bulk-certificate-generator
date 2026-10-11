class StorageError(Exception):
    """Transient storage failure (disk full, IO error). Safe to retry."""

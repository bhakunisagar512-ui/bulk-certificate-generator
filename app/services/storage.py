"""File storage behind a small interface so S3 can be dropped in later."""

import hashlib
import os
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO, Protocol

from app.config import get_settings
from app.services.exceptions import StorageError


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int
    sha256: str


class Storage(Protocol):
    def save(self, key: str, content: bytes) -> StoredFile: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        path = (self.base / key).resolve()
        if not path.is_relative_to(self.base) or path == self.base:
            raise ValueError("invalid storage key")  # blocks ../ traversal and absolute keys
        return path

    def save(self, key: str, content: bytes) -> StoredFile:
        path = self._resolve(key)
        tmp = path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)  # atomic: readers never see a half-written file
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(str(exc)) from exc
        return StoredFile(key=key, size=len(content), sha256=hashlib.sha256(content).hexdigest())

    def open(self, key: str) -> BinaryIO:
        return open(self._resolve(key), "rb")

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)


@lru_cache
def get_storage() -> LocalStorage:
    return LocalStorage(get_settings().storage_dir)


def storage_key(job_id: uuid.UUID, certificate_id: uuid.UUID) -> str:
    """Server-generated keys only: user input never reaches the file system."""
    return f"{job_id}/{certificate_id}.pdf"

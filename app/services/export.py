"""Download helpers: safe filenames, streaming, ZIP bundles."""

import csv
import io
import re
import tempfile
import zipfile
from collections.abc import Iterator
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Certificate, CertStatus, Job
from app.services.storage import Storage

CHUNK = 64 * 1024


def safe_filename(name: str | None, fallback: str = "certificate") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()).strip("._")[:50]
    return cleaned or fallback


def csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (=, +, -, @ at the start of a cell)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def iter_file(fh: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := fh.read(CHUNK):
            yield chunk
    finally:
        fh.close()


def build_zip(db: Session, storage: Storage, job: Job) -> BinaryIO:
    """ZIP of all succeeded certificates plus summary.csv. Spools to disk past 10 MB so
    memory use stays flat for large jobs."""
    tmp = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    summary = io.StringIO()
    writer = csv.writer(summary)
    writer.writerow(["index", "name", "email", "status", "error_code", "error_message", "file"])

    rows = db.scalars(select(Certificate).where(Certificate.job_id == job.id).order_by(Certificate.row_index)).all()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for cert in rows:
            arcname = ""
            if cert.status == CertStatus.SUCCEEDED.value and cert.file_path:
                arcname = f"{cert.row_index:04d}_{safe_filename(cert.recipient_name)}.pdf"
                try:
                    with storage.open(cert.file_path) as fh:
                        zf.writestr(arcname, fh.read())
                except FileNotFoundError:
                    arcname = ""
            writer.writerow(
                [
                    cert.row_index,
                    csv_safe(cert.recipient_name),
                    csv_safe(cert.recipient_email),
                    cert.status,
                    cert.error_code or "",
                    csv_safe(cert.error_message),
                    arcname,
                ]
            )
        zf.writestr("summary.csv", summary.getvalue())
    tmp.seek(0)
    return tmp

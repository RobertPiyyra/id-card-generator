"""
File I/O utilities.

Provides atomic file writing and robust file reading for uploads.
Extracted from legacy_app.py.
"""

import io
import logging
import os
import uuid

logger = logging.getLogger(__name__)


def _read_uploaded_file_bytes(file_storage, *, file_label="file"):
    """Read uploaded bytes robustly so we never silently persist empty files."""
    if file_storage is None or not getattr(file_storage, "filename", ""):
        raise ValueError(f"{file_label.capitalize()} is required.")

    raw_bytes = b""
    try:
        stream = getattr(file_storage, "stream", None)
        if stream is not None:
            try:
                stream.seek(0)
            except Exception:
                pass
        raw_bytes = file_storage.read() or b""
        if not raw_bytes and hasattr(file_storage, "save"):
            buffer = io.BytesIO()
            file_storage.save(buffer)
            raw_bytes = buffer.getvalue() or b""
    finally:
        stream = getattr(file_storage, "stream", None)
        if stream is not None:
            try:
                stream.seek(0)
            except Exception:
                pass

    if not raw_bytes:
        raise ValueError(f"Uploaded {file_label} is empty. Please choose the image again.")
    return raw_bytes


def _write_binary_file_atomic(path, payload):
    """Write bytes atomically and refuse empty output files."""
    data = payload if isinstance(payload, bytes) else bytes(payload or b"")
    if not data:
        raise ValueError(f"Refusing to write empty file: {os.path.basename(path)}")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp_{uuid.uuid4().hex}"
    with open(tmp_path, "wb") as fh:
        fh.write(data)
    if os.path.getsize(tmp_path) <= 0:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise ValueError(f"Failed to save photo bytes for {os.path.basename(path)}")
    os.replace(tmp_path, path)
    return path

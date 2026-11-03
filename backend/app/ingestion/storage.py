"""Uploaded PDFs on local disk, behind the `Storage` port (`app/ports.py`).

Layout: `<root>/<tenant_id>/<document_id>.pdf`. The key stored in the database is the
path relative to the root, so the root can move (e.g. another volume) without a migration.
"""

import os
import tempfile
import uuid
from pathlib import Path


class LocalDiskStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def save(self, tenant_id: uuid.UUID, document_id: uuid.UUID, data: bytes) -> str:
        key = f"{tenant_id}/{document_id}.pdf"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write: a temporary file in the same directory, then rename. A reader (the
        # worker) never sees a half-written PDF, even if this process dies midway.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(data)
                tmp.flush()
                os.fsync(tmp.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return key

    def read(self, key: str) -> bytes:
        """Raises `FileNotFoundError` if the file does not exist."""
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """Deleting a missing file is not an error (deletes are retried and repeated)."""
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        """Resolve a key to a path under the root, rejecting anything that escapes it
        (`..`, absolute paths, symlinks pointing outside)."""
        if not key or Path(key).is_absolute():
            raise ValueError("invalid storage key")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("invalid storage key")
        return path

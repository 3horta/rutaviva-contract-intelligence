import uuid
from pathlib import Path

import pytest

from app.ingestion.storage import LocalDiskStorage


def test_save_read_delete_roundtrip(tmp_path: Path) -> None:
    storage = LocalDiskStorage(tmp_path / "files")
    tenant_id, document_id = uuid.uuid4(), uuid.uuid4()

    key = storage.save(tenant_id, document_id, b"%PDF-1.4 data")

    assert key == f"{tenant_id}/{document_id}.pdf"
    assert storage.read(key) == b"%PDF-1.4 data"
    # No temporary files left behind by the atomic write.
    assert [p.name for p in (tmp_path / "files" / str(tenant_id)).iterdir()] == [
        f"{document_id}.pdf"
    ]

    storage.delete(key)
    with pytest.raises(FileNotFoundError):
        storage.read(key)


def test_deleting_a_missing_file_is_fine(tmp_path: Path) -> None:
    LocalDiskStorage(tmp_path).delete(f"{uuid.uuid4()}/{uuid.uuid4()}.pdf")


@pytest.mark.parametrize(
    "key",
    ["../outside.pdf", "a/../../outside.pdf", "/etc/passwd", "", "."],
)
def test_keys_that_escape_the_root_are_rejected(tmp_path: Path, key: str) -> None:
    storage = LocalDiskStorage(tmp_path / "files")

    with pytest.raises(ValueError):
        storage.read(key)
    with pytest.raises(ValueError):
        storage.delete(key)


def test_symlink_pointing_outside_the_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (tmp_path / "secret.pdf").write_bytes(b"secret")
    (root / "link.pdf").symlink_to(tmp_path / "secret.pdf")

    with pytest.raises(ValueError):
        LocalDiskStorage(root).read("link.pdf")

"""T-M0-4."""

import hashlib

import pytest

from app.blob.base import BlobStore, IntegrityError, key_for
from app.blob.fake import FakeBlobStore


def test_put_get_roundtrip_and_hash() -> None:
    store: BlobStore = FakeBlobStore()
    ref = store.put(b"scan-bytes")
    assert ref.sha256 == hashlib.sha256(b"scan-bytes").hexdigest()
    assert ref.size == 10 and ref.key == key_for(ref.sha256)
    assert store.get(ref) == b"scan-bytes"
    assert store.head(ref) == 10 and store.exists(ref.sha256)


def test_dedupe_same_content_one_object() -> None:
    store = FakeBlobStore()
    a, b = store.put(b"x"), store.put(b"x")
    assert a == b and len(store._objects) == 1  # noqa: SLF001


def test_missing_object() -> None:
    store = FakeBlobStore()
    ref = FakeBlobStore().put(b"other")
    assert store.head(ref) is None and not store.exists(ref.sha256)


def test_interface_has_no_delete_or_overwrite() -> None:
    for name in ("delete", "remove", "overwrite", "update", "put_object", "replace"):
        assert not hasattr(BlobStore, name)
        assert not hasattr(FakeBlobStore, name)


def test_integrity_check_on_read() -> None:
    store = FakeBlobStore()
    ref = store.put(b"good")
    store._objects[ref.key] = b"tampered"  # noqa: SLF001
    with pytest.raises(IntegrityError):
        store.get(ref)

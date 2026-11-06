"""In-memory BlobStore used by tests and the demo profile."""

import hashlib
from collections.abc import Iterator

from app.blob.base import BlobRef, IntegrityError, key_for


class FakeBlobStore:
    def __init__(self, bucket: str = "originals") -> None:
        self._bucket = bucket
        self._objects: dict[str, bytes] = {}

    def put(self, data: bytes) -> BlobRef:
        digest = hashlib.sha256(data).hexdigest()
        key = key_for(digest)
        # Write-once: an existing object with this hash is identical by construction.
        self._objects.setdefault(key, data)
        return BlobRef(sha256=digest, size=len(data), bucket=self._bucket, key=key)

    def get(self, ref: BlobRef) -> bytes:
        data = self._objects[ref.key]
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise IntegrityError("blob integrity check failed")
        return data

    def head(self, ref: BlobRef) -> int | None:
        obj = self._objects.get(ref.key)
        return None if obj is None else len(obj)

    def exists(self, sha256: str) -> bool:
        return key_for(sha256) in self._objects

    def iter_keys(self) -> Iterator[str]:
        yield from sorted(self._objects)

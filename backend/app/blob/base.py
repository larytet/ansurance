"""BlobStore contract.

Originals are write-once and content-addressed. There is deliberately no delete
and no overwrite on this interface.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size: int
    bucket: str
    key: str


class BlobStore(Protocol):
    def put(self, data: bytes) -> BlobRef: ...

    def get(self, ref: BlobRef) -> bytes: ...

    def head(self, ref: BlobRef) -> int | None:
        """Size in bytes, or None when the object does not exist."""
        ...

    def exists(self, sha256: str) -> bool: ...


def key_for(sha256: str) -> str:
    return f"sha256/{sha256[:2]}/{sha256}"

"""Compare the object store with the database. Reports only; never deletes an original."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session as Db

from app.blob.base import BlobStore
from app.models import Blob


@dataclass
class Report:
    orphans: list[str] = field(default_factory=list)  # object without a database row
    missing: list[str] = field(default_factory=list)  # database row without an object

    @property
    def ok(self) -> bool:
        return not self.orphans and not self.missing


def reconcile(db: Db, store: BlobStore, bucket: str) -> Report:
    keys = set(store.iter_keys())
    rows = {b.key for b in db.scalars(select(Blob).where(Blob.bucket == bucket))}
    return Report(orphans=sorted(keys - rows), missing=sorted(rows - keys))

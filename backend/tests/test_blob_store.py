"""T-M2-1, T-M2-3 (store layer), T-M2-9: the BlobStore contract against every
implementation. The S3 variant runs against real MinIO built from source (the D11 spike)."""

import hashlib

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.blob.base import BlobRef, BlobStore, IntegrityError, key_for
from app.blob.fake import FakeBlobStore
from app.blob.s3 import S3BlobStore
from app.config import Settings
from tests.conftest import MinioInfo


@pytest.fixture(params=["fake", "s3"])
def store(request: pytest.FixtureRequest) -> BlobStore:
    if request.param == "fake":
        return FakeBlobStore()
    s3_settings: Settings = request.getfixturevalue("s3_settings")
    return S3BlobStore(s3_settings)


def test_put_get_roundtrip_hash_and_key(store: BlobStore) -> None:
    data = b"%PDF-1.4 scan " + hashlib.sha256(b"x").digest()
    ref = store.put(data)
    assert ref.sha256 == hashlib.sha256(data).hexdigest() and ref.size == len(data)
    assert ref.key == key_for(ref.sha256)
    assert store.get(ref) == data and store.head(ref) == len(data) and store.exists(ref.sha256)


def test_same_content_is_stored_once(store: BlobStore) -> None:
    a, b = store.put(b"same bytes"), store.put(b"same bytes")
    assert a == b
    assert [k for k in store.iter_keys() if k == a.key] == [a.key]


def test_unknown_object(store: BlobStore) -> None:
    ghost = BlobRef("0" * 64, 1, "originals", key_for("0" * 64))
    assert store.head(ghost) is None and not store.exists("0" * 64)


def test_large_object(store: BlobStore) -> None:
    data = bytes(range(256)) * 40_000  # about 10 MB
    assert store.get(store.put(data)) == data


def test_interface_has_no_delete_or_overwrite(store: BlobStore) -> None:
    for name in ("delete", "remove", "overwrite", "update", "replace", "delete_object"):
        assert not hasattr(store, name)


def test_listing_includes_new_keys(store: BlobStore) -> None:
    ref = store.put(b"list me")
    assert ref.key in set(store.iter_keys())


# S3 only: write-once is enforced by the store, not just by our code -------------------------
def _raw(minio: MinioInfo, user: str, password: str):  # type: ignore[no-untyped-def]
    return boto3.client(
        "s3",
        endpoint_url=minio.endpoint,
        aws_access_key_id=user,
        aws_secret_access_key=password,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def test_application_credentials_cannot_delete_or_bypass_lock(
    s3_settings: Settings, minio: MinioInfo
) -> None:
    ref = S3BlobStore(s3_settings).put(b"keep me forever")
    app = _raw(minio, minio.app_user, minio.app_password)
    ver = app.head_object(Bucket=ref.bucket, Key=ref.key)["VersionId"]
    for call in (
        lambda: app.delete_object(Bucket=ref.bucket, Key=ref.key),
        lambda: app.delete_object(Bucket=ref.bucket, Key=ref.key, VersionId=ver),
        lambda: app.delete_object(
            Bucket=ref.bucket, Key=ref.key, VersionId=ver, BypassGovernanceRetention=True
        ),
    ):
        with pytest.raises(ClientError) as e:
            call()
        assert e.value.response["Error"]["Code"] == "AccessDenied"
    assert S3BlobStore(s3_settings).exists(ref.sha256)


def test_object_lock_refuses_version_delete_even_for_root(
    s3_settings: Settings, minio: MinioInfo
) -> None:
    ref = S3BlobStore(s3_settings).put(b"locked by retention")
    root = _raw(minio, minio.root_user, minio.root_password)
    ver = root.head_object(Bucket=ref.bucket, Key=ref.key)["VersionId"]
    with pytest.raises(ClientError) as e:
        root.delete_object(Bucket=ref.bucket, Key=ref.key, VersionId=ver)
    assert e.value.response["Error"]["Code"] == "InvalidRequest"
    assert (
        root.get_object_retention(Bucket=ref.bucket, Key=ref.key, VersionId=ver)["Retention"][
            "Mode"
        ]
        == "GOVERNANCE"
    )


def test_second_put_of_same_key_does_not_replace(s3_settings: Settings, minio: MinioInfo) -> None:
    store = S3BlobStore(s3_settings)
    ref = store.put(b"original content")
    app = _raw(minio, minio.app_user, minio.app_password)
    with pytest.raises(ClientError) as e:  # a raw attempt to replace the same key
        app.put_object(Bucket=ref.bucket, Key=ref.key, Body=b"evil", IfNoneMatch="*")
    assert e.value.response["Error"]["Code"] == "PreconditionFailed"
    assert store.get(ref) == b"original content"


def test_integrity_failure_detected_when_stored_bytes_change(
    s3_settings: Settings, minio: MinioInfo
) -> None:
    """Simulates storage corruption or an admin replacing the object with new bytes."""
    store = S3BlobStore(s3_settings)
    ref = store.put(b"genuine scan bytes")
    root = _raw(minio, minio.root_user, minio.root_password)
    root.put_object(Bucket=ref.bucket, Key=ref.key, Body=b"corrupted")
    with pytest.raises(IntegrityError):
        store.get(ref)

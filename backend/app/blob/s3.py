"""BlobStore on an S3 API (MinIO on-prem, Amazon S3 in AWS).

Write-once: objects are content-addressed, put with If-None-Match so an existing key is
never replaced, and there is no delete or overwrite method. The application credentials
carry no delete permission and the bucket has Object Lock (deploy/objectstore).
"""

import hashlib
from collections.abc import Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.blob.base import BlobRef, IntegrityError, key_for
from app.config import Settings


class S3BlobStore:
    def __init__(self, settings: Settings, bucket: str | None = None) -> None:
        self._bucket = bucket or settings.s3_bucket_originals
        access = settings.s3_access_key
        secret = settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else None
        self._s3 = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint or None,
            region_name=settings.s3_region,
            aws_access_key_id=access or None,
            aws_secret_access_key=secret,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def put(self, data: bytes) -> BlobRef:
        digest = hashlib.sha256(data).hexdigest()
        key = key_for(digest)
        try:
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, IfNoneMatch="*")
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("PreconditionFailed", "412"):
                raise
            # already stored: identical by construction (content-addressed)
        return BlobRef(sha256=digest, size=len(data), bucket=self._bucket, key=key)

    def get(self, ref: BlobRef) -> bytes:
        data: bytes = self._s3.get_object(Bucket=ref.bucket, Key=ref.key)["Body"].read()
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise IntegrityError("blob integrity check failed")
        return data

    def head(self, ref: BlobRef) -> int | None:
        return self._head(ref.bucket, ref.key)

    def exists(self, sha256: str) -> bool:
        return self._head(self._bucket, key_for(sha256)) is not None

    def _head(self, bucket: str, key: str) -> int | None:
        try:
            return int(self._s3.head_object(Bucket=bucket, Key=key)["ContentLength"])
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def iter_keys(self) -> Iterator[str]:
        for page in self._s3.get_paginator("list_objects_v2").paginate(Bucket=self._bucket):
            for obj in page.get("Contents", []):
                yield obj["Key"]

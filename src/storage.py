"""S3-compatible file storage client (AWS S3, MinIO, Cloudflare R2, ...).

boto3 is sync-only; `upload` wraps the blocking call in a thread so it's
safe to await from FastAPI's async handlers. Credentials come from
`settings.storage_*` — all empty by default, so `upload` fails loudly
until they're filled in rather than silently writing nowhere.
"""

import asyncio
import uuid

import boto3

from .settings import settings


class StorageNotConfiguredError(RuntimeError):
    pass


class StorageClient:
    def _make_s3_client(self):
        return boto3.client(
            "s3",
            endpoint_url=settings.storage_base_url,
            aws_access_key_id=settings.storage_access_key,
            aws_secret_access_key=settings.storage_secret_key,
            region_name=settings.storage_region,
        )

    async def upload(
        self, filename: str, content: bytes, content_type: str, *, public: bool = False
    ) -> str:
        """Stores a file and returns the URL it is addressed by.

        `public` decides whether anonymous readers can fetch it, and the default
        is no. Papers must stay unreadable to anyone without a session — blind
        review is worth nothing if the PDF is a guessable URL away — while
        landing-page images are loaded by `<img src>` from a browser that has no
        credentials, so those have to be public to render at all.
        """
        if not settings.storage_base_url or not settings.storage_bucket:
            raise StorageNotConfiguredError(
                "storage is not configured — set storage_base_url and storage_bucket"
            )

        key = f"{uuid.uuid4()}-{filename}"

        def _put() -> None:
            s3 = self._make_s3_client()
            extra = {"ACL": "public-read"} if public else {}
            s3.put_object(
                Bucket=settings.storage_bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                **extra,
            )

        await asyncio.to_thread(_put)
        return f"{settings.storage_base_url}/{settings.storage_bucket}/{key}"


client = StorageClient()

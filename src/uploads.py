"""Shared PDF upload handling.

Both `POST /uploads` (standalone, used before an article exists) and
`POST /articles/{id}/upload` (legacy, kept for existing callers) validate and
store a file the same way — the rules live here so the two cannot drift apart.
"""

from fastapi import HTTPException, UploadFile, status

from . import storage
from .settings import settings

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024


def exceeds_upload_limit(content: bytes) -> bool:
    return len(content) > MAX_UPLOAD_BYTES


async def store_pdf(file: UploadFile) -> str:
    """Validates a PDF upload and stores it, returning its storage path.

    Raises HTTPException with the same status codes both endpoints promise:
    400 for a non-PDF, 413 for oversize, 500 when storage is unconfigured.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PDF files are accepted")

    # Read one byte past the limit: enough to detect an oversized upload
    # without ever buffering the whole thing.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if exceeds_upload_limit(content):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file too large (max {settings.max_upload_mb} MB)",
        )

    try:
        return await storage.client.upload(
            file.filename or "upload.pdf", content, file.content_type
        )
    except storage.StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))

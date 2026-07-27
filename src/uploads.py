"""Shared upload handling.

Papers (`POST /uploads`, and the legacy `POST /articles/{id}/upload`) and
landing-page images (`POST /uploads/image`) validate and store files the same
way — the rules live here so the callers cannot drift apart.
"""

from fastapi import HTTPException, UploadFile, status

from . import storage
from .settings import settings

MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024

# Raster formats every browser renders, plus WebP for the smaller payloads the
# landing page prefers. SVG is excluded on purpose: it is a script-carrying
# document, and these files are served back to visitors.
ALLOWED_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


def exceeds_upload_limit(content: bytes) -> bool:
    return len(content) > MAX_UPLOAD_BYTES


async def _read_within_limit(file: UploadFile) -> bytes:
    # Read one byte past the limit: enough to detect an oversized upload
    # without ever buffering the whole thing.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if exceeds_upload_limit(content):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file too large (max {settings.max_upload_mb} MB)",
        )
    return content


async def _store(
    file: UploadFile, content: bytes, fallback_name: str, *, public: bool = False
) -> str:
    try:
        return await storage.client.upload(
            file.filename or fallback_name, content, file.content_type, public=public
        )
    except storage.StorageNotConfiguredError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))


async def store_pdf(file: UploadFile) -> str:
    """Validates a PDF upload and stores it, returning its storage path.

    Raises HTTPException with the same status codes both endpoints promise:
    400 for a non-PDF, 413 for oversize, 500 when storage is unconfigured.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "only PDF files are accepted")

    content = await _read_within_limit(file)
    return await _store(file, content, "upload.pdf")


async def store_image(file: UploadFile) -> str:
    """Validates an image upload and stores it, returning its storage path.

    Same size ceiling and failure codes as `store_pdf`; only the accepted
    content types differ.
    """
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "only PNG, JPEG, WebP or GIF images are accepted",
        )

    content = await _read_within_limit(file)
    # Public: the landing page loads these with a plain <img src> from a browser
    # carrying no credentials. Papers, stored by store_pdf above, stay private.
    return await _store(file, content, "upload.png", public=True)

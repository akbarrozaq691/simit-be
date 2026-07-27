"""Upload size limiting lives in `src/uploads.py`, shared by both
`POST /uploads` and the legacy `POST /articles/{id}/upload`. These tests
exercise the size check directly — the endpoints themselves need a live app
plus configured storage, covered by manual verification."""

import pytest
from fastapi import HTTPException

from src import uploads
from src.settings import settings

# Derived, not hardcoded: MAX_UPLOAD_MB may be overridden via .env, and these
# tests assert the wiring and the boundary, not the specific default.
LIMIT = settings.max_upload_mb * 1024 * 1024


def test_default_limit_is_10_mb_unless_overridden():
    """Documents the intended default. Skips rather than fails if the local
    environment overrides it, since that's a valid configuration."""
    if settings.max_upload_mb != 10:
        pytest.skip(f"MAX_UPLOAD_MB overridden to {settings.max_upload_mb}")
    assert uploads.MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_max_upload_bytes_derived_from_settings():
    assert uploads.MAX_UPLOAD_BYTES == LIMIT


@pytest.mark.parametrize("offset,should_reject", [(-LIMIT + 1, False), (-1, False), (0, False), (1, True)])
def test_exceeds_limit_boundary(offset, should_reject):
    content = b"x" * (LIMIT + offset)
    assert uploads.exceeds_upload_limit(content) is should_reject


class _StubUpload:
    """Minimal stand-in for Starlette's UploadFile: only the attributes the
    validation path in `src/uploads.py` actually touches."""

    def __init__(self, content_type: str | None, filename: str = "f") -> None:
        self.content_type = content_type
        self.filename = filename

    async def read(self, size: int) -> bytes:
        return b""


@pytest.mark.parametrize(
    "content_type", ["image/png", "image/jpeg", "image/webp", "image/gif"]
)
def test_allowed_image_types(content_type):
    assert content_type in uploads.ALLOWED_IMAGE_TYPES


@pytest.mark.parametrize(
    "content_type",
    [
        # SVG carries script and these files are served back to visitors.
        "image/svg+xml",
        "application/pdf",
        "text/html",
        "application/octet-stream",
        None,
    ],
)
def test_rejected_image_types(content_type):
    assert content_type not in uploads.ALLOWED_IMAGE_TYPES


@pytest.mark.asyncio
async def test_store_image_rejects_non_image():
    with pytest.raises(HTTPException) as exc:
        await uploads.store_image(_StubUpload("image/svg+xml"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_store_pdf_rejects_image():
    """The two entry points must not have merged into a permissive one."""
    with pytest.raises(HTTPException) as exc:
        await uploads.store_pdf(_StubUpload("image/png"))
    assert exc.value.status_code == 400

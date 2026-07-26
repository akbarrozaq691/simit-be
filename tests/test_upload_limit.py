"""The upload endpoint must reject oversized files with 413 without buffering
the whole body. These tests exercise the size-check helper directly — the
endpoint itself needs a live app + storage, covered by manual verification."""

import pytest

from src.routers.articles import router as articles_router
from src.settings import settings

# Derived, not hardcoded: MAX_UPLOAD_MB may be overridden via .env, and these
# tests assert the wiring and the boundary, not the specific default.
LIMIT = settings.max_upload_mb * 1024 * 1024


def test_default_limit_is_10_mb_unless_overridden():
    """Documents the intended default. Skips rather than fails if the local
    environment overrides it, since that's a valid configuration."""
    if settings.max_upload_mb != 10:
        pytest.skip(f"MAX_UPLOAD_MB overridden to {settings.max_upload_mb}")
    assert articles_router.MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_max_upload_bytes_derived_from_settings():
    assert articles_router.MAX_UPLOAD_BYTES == LIMIT


@pytest.mark.parametrize("offset,should_reject", [(-LIMIT + 1, False), (-1, False), (0, False), (1, True)])
def test_exceeds_limit_boundary(offset, should_reject):
    content = b"x" * (LIMIT + offset)
    assert articles_router._exceeds_upload_limit(content) is should_reject

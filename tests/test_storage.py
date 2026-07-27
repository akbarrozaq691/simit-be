from unittest.mock import MagicMock, patch

import pytest

from src import storage


@pytest.mark.asyncio
async def test_upload_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "")
    client = storage.StorageClient()
    with pytest.raises(storage.StorageNotConfiguredError):
        await client.upload("paper.pdf", b"%PDF-1.4...", "application/pdf")


@pytest.mark.asyncio
async def test_upload_calls_boto3_put_object_and_returns_url(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "https://storage.example.com")
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")
    monkeypatch.setattr(storage.settings, "storage_access_key", "key")
    monkeypatch.setattr(storage.settings, "storage_secret_key", "secret")
    monkeypatch.setattr(storage.settings, "storage_region", "auto")

    mock_s3 = MagicMock()
    with patch.object(storage.boto3, "client", return_value=mock_s3) as mock_boto_client:
        client = storage.StorageClient()
        result = await client.upload("paper.pdf", b"%PDF-1.4...", "application/pdf")

    mock_boto_client.assert_called_once_with(
        "s3",
        endpoint_url="https://storage.example.com",
        aws_access_key_id="key",
        aws_secret_access_key="secret",
        region_name="auto",
    )
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args.kwargs
    assert call_kwargs["Bucket"] == "papers"
    assert call_kwargs["Body"] == b"%PDF-1.4..."
    assert call_kwargs["ContentType"] == "application/pdf"
    assert call_kwargs["Key"].endswith("paper.pdf")
    assert result == f"https://storage.example.com/papers/{call_kwargs['Key']}"


# ---- object visibility ----


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "public,expected_acl",
    [(True, "public-read"), (False, None)],
)
async def test_upload_sets_acl_only_when_public(monkeypatch, public, expected_acl):
    """Images must be readable by a credential-less browser; papers must not be
    readable by anyone without a session."""
    monkeypatch.setattr(storage.settings, "storage_base_url", "https://storage.example.com")
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")

    mock_s3 = MagicMock()
    with patch.object(storage.boto3, "client", return_value=mock_s3):
        await storage.StorageClient().upload("f.png", b"x", "image/png", public=public)

    kwargs = mock_s3.put_object.call_args.kwargs
    assert kwargs.get("ACL") == expected_acl


# ---- presigned downloads ----


def test_key_for_recovers_the_object_key(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")
    url = "https://storage.example.com/papers/abc-123-paper.pdf"
    assert storage.key_for(url) == "abc-123-paper.pdf"


def test_key_for_survives_a_bucket_name_appearing_in_the_host(monkeypatch):
    """Splitting on the bucket segment, not on a fixed prefix, keeps the key
    intact when the bucket name also occurs earlier in the URL."""
    monkeypatch.setattr(storage.settings, "storage_bucket", "ppit")
    url = "https://ppit.example.com/ppit/xyz-paper.pdf"
    assert storage.key_for(url) == "xyz-paper.pdf"


def test_key_for_rejects_a_path_from_another_bucket(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")
    with pytest.raises(storage.UnsignableFileError):
        storage.key_for("https://storage.example.com/other-bucket/abc-paper.pdf")


def test_key_for_rejects_a_placeholder_path(monkeypatch):
    """Rows written before storage existed hold bare filenames."""
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")
    with pytest.raises(storage.UnsignableFileError):
        storage.key_for("placeholder.pdf")


@pytest.mark.asyncio
async def test_presigned_url_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "")
    with pytest.raises(storage.StorageNotConfiguredError):
        await storage.StorageClient().presigned_url("https://x/papers/a.pdf", 300)


@pytest.mark.asyncio
async def test_presigned_url_signs_get_object_with_the_ttl(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_base_url", "https://storage.example.com")
    monkeypatch.setattr(storage.settings, "storage_bucket", "papers")

    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://signed.example.com/a.pdf?sig=1"
    with patch.object(storage.boto3, "client", return_value=mock_s3):
        url = await storage.StorageClient().presigned_url(
            "https://storage.example.com/papers/abc-paper.pdf", 300
        )

    assert url == "https://signed.example.com/a.pdf?sig=1"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "papers", "Key": "abc-paper.pdf"},
        ExpiresIn=300,
    )

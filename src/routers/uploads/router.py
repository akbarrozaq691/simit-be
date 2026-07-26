from fastapi import APIRouter, Depends, File, UploadFile

from ... import uploads
from ...deps import get_current_user
from ...schemas import UploadResponse, UserCtx

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", response_model=UploadResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    user: UserCtx = Depends(get_current_user),
) -> UploadResponse:
    """Stores a PDF and returns its path, without needing an article to exist.

    This is what makes a clean submit flow possible: `POST /articles` requires
    `abstract_file_path`, so a client must obtain a real path BEFORE creating
    the article. The older `POST /articles/{id}/upload` cannot serve that —
    it needs an article id — which forced callers into creating a row with a
    placeholder path and patching it afterwards.

    Any authenticated user may upload; the returned path is only meaningful
    once passed into a create/full-paper/revision request, each of which
    enforces its own ownership rules.
    """
    path = await uploads.store_pdf(file)
    return UploadResponse(file_path=path)

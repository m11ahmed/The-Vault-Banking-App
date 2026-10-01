"""Document-processing assistance. An analysis never approves or modifies KYC."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..kyc_service import MAX_FILE_BYTES, analyze_document
from ..auth import require_customer, owned_account

router = APIRouter(prefix="/kyc", tags=["KYC document analysis"])


@router.post("/{account_id}/analyze-document")
def analyze_kyc_document(
    account_id: str,
    file: UploadFile = File(...),
    expected_id_number: str | None = Form(default=None, max_length=80),
    db: Session = Depends(get_db),
    user: models.User = Depends(require_customer),
):
    """Compare local OCR results with an account and optional existing KYC record.

    Accepts JPEG/PNG/WEBP (5 MB, 12 megapixels) or a UTF-8 TXT demo sample.
    Results require a human reviewer and do not establish identity or authenticity.
    Uploaded image bytes are not retained by this application.
    """
    try:
        account = owned_account(db, account_id, user)
        expected_id = (expected_id_number or "").strip() or None
        if expected_id is None and account.kyc_record:
            expected_id = account.kyc_record.id_number
        content = file.file.read(MAX_FILE_BYTES + 1)
        return analyze_document(account.id, account.owner_name, expected_id, file.filename, content)
    finally:
        file.file.close()

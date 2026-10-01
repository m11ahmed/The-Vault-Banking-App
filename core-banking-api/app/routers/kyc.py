"""Customer submissions and explicit, audited human KYC decisions."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import get_current_user, owned_account, require_customer, require_reviewer
from ..database import get_db

router = APIRouter(prefix="/kyc", tags=["KYC"])


def _with_owner(record: models.KYCRecord, owner_name: str):
    # Response context, not another persisted copy of the customer's name.
    record.owner_name = owner_name
    return record


def _readable_account(db: Session, account_id: str, user: models.User):
    if getattr(user.role, "value", user.role) != "reviewer":
        return owned_account(db, account_id, user)
    account = db.get(models.Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


# Register the queue before /{account_id}, so "reviews" is never an account ID.
@router.get("/reviews/pending", response_model=list[schemas.KYCOut])
def pending_reviews(
    db: Session = Depends(get_db),
    reviewer: models.User = Depends(require_reviewer),
):
    rows = (
        db.query(models.KYCRecord, models.Account.owner_name)
        .join(models.Account, models.Account.id == models.KYCRecord.account_id)
        .filter(models.KYCRecord.status == models.KYCStatus.pending)
        .order_by(models.KYCRecord.submitted_at.asc(), models.KYCRecord.id.asc())
        .all()
    )
    return [_with_owner(record, owner_name) for record, owner_name in rows]


@router.post("/{account_id}", response_model=schemas.KYCOut, status_code=201)
def submit_kyc(
    account_id: str,
    payload: schemas.KYCSubmit,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_customer),
):
    account = owned_account(db, account_id, user)
    record = db.query(models.KYCRecord).filter(models.KYCRecord.account_id == account_id).first()
    submitted_at = datetime.now(timezone.utc)
    if record is not None:
        if record.status != models.KYCStatus.rejected:
            raise HTTPException(status_code=400, detail="KYC is already pending or verified for this account.")
        # A concurrent resubmission cannot overwrite a newer pending submission.
        changed = (
            db.query(models.KYCRecord)
            .filter(
                models.KYCRecord.id == record.id,
                models.KYCRecord.status == models.KYCStatus.rejected,
                models.KYCRecord.submitted_at == record.submitted_at,
            )
            .update(
                {
                    "full_name": payload.full_name,
                    "id_number": payload.id_number,
                    "document_type": payload.document_type,
                    "status": models.KYCStatus.pending,
                    "submitted_at": submitted_at,
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "review_reason": None,
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="KYC was already resubmitted. Refresh its status.")
    else:
        record = models.KYCRecord(
            account_id=account_id,
            full_name=payload.full_name,
            id_number=payload.id_number,
            document_type=payload.document_type,
            status=models.KYCStatus.pending,
            submitted_at=submitted_at,
        )
        db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="KYC was already submitted for this account.") from None
    db.refresh(record)
    return _with_owner(record, account.owner_name)


@router.get("/{account_id}", response_model=schemas.KYCOut)
def get_kyc_status(
    account_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    account = _readable_account(db, account_id, user)
    record = db.query(models.KYCRecord).filter(models.KYCRecord.account_id == account_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="No KYC record found for this account.")
    return _with_owner(record, account.owner_name)


@router.patch("/{account_id}/verify", response_model=schemas.KYCOut)
def verify_kyc(
    account_id: str,
    payload: schemas.KYCReview,
    db: Session = Depends(get_db),
    reviewer: models.User = Depends(require_reviewer),
):
    """Only a human reviewer can decide a pending submission, with an audit reason."""
    account = _readable_account(db, account_id, reviewer)
    record = db.query(models.KYCRecord).filter(models.KYCRecord.account_id == account_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="No KYC record found for this account.")

    expected_version = payload.expected_submitted_at
    if expected_version.tzinfo is not None:
        expected_version = expected_version.astimezone(timezone.utc).replace(tzinfo=None)
    current_version = record.submitted_at
    if current_version.tzinfo is not None:
        current_version = current_version.astimezone(timezone.utc).replace(tzinfo=None)
    if expected_version != current_version:
        raise HTTPException(status_code=409, detail="This submission has changed. Refresh it before reviewing.")

    reason = payload.reason.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="A review reason of at least 3 characters is required.")
    decision = models.KYCStatus.verified if payload.approve else models.KYCStatus.rejected
    reviewed_at = datetime.now(timezone.utc)
    # Only one reviewer can consume a pending record, even for concurrent requests.
    changed = (
        db.query(models.KYCRecord)
        .filter(
            models.KYCRecord.id == record.id,
            models.KYCRecord.status == models.KYCStatus.pending,
            models.KYCRecord.submitted_at == record.submitted_at,
        )
        .update(
            {
                "status": decision,
                "reviewed_at": reviewed_at,
                "reviewed_by": reviewer.id,
                "review_reason": reason,
            },
            synchronize_session=False,
        )
    )
    if changed != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="Only a pending KYC submission can be reviewed.")
    db.add(
        models.KYCReviewAudit(
            record_id=record.id,
            reviewer_id=reviewer.id,
            decision=decision.value,
            reason=reason,
            created_at=reviewed_at,
        )
    )
    # The decision and audit entry either both persist or both roll back.
    db.commit()
    db.refresh(record)
    return _with_owner(record, account.owner_name)


@router.get("/{account_id}/reviews", response_model=list[schemas.KYCReviewAuditOut])
def review_history(
    account_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    _readable_account(db, account_id, user)
    record = db.query(models.KYCRecord).filter(models.KYCRecord.account_id == account_id).first()
    if record is None:
        raise HTTPException(status_code=404, detail="No KYC record found for this account.")
    return (
        db.query(models.KYCReviewAudit)
        .filter(models.KYCReviewAudit.record_id == record.id)
        .order_by(models.KYCReviewAudit.created_at.desc(), models.KYCReviewAudit.id.desc())
        .all()
    )

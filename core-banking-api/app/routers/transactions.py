"""Authenticated, atomic money operations with persisted retry protection."""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import owned_account, require_customer
from ..database import get_db
from ..money import MAX_MINOR, parse_minor

router = APIRouter(tags=["Transactions"])


def _get_account_or_404(db: Session, account_id: str) -> models.Account:
    account = db.query(models.Account).filter(models.Account.id == account_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


def _key(value: str | None) -> str:
    if value is None or not value.strip():
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for money operations.")
    if len(value) > 100 or value != value.strip() or any(ord(char) < 32 for char in value):
        raise HTTPException(status_code=400, detail="Idempotency-Key must contain 1 to 100 printable characters without outer whitespace.")
    return value


def _fingerprint(operation: str, account_ids: list[str], amount_minor: int, note: str | None) -> str:
    canonical = json.dumps(
        {"operation": operation, "accounts": account_ids, "amount_minor": amount_minor, "note": note},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _begin(db: Session, user_id: str) -> None:
    # Authentication has read using this session. Finish it before reserving
    # SQLite's writer lock, which must precede all business reads.
    db.commit()
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    else:
        # Serialize a user's requests before checking a new idempotency key.
        db.query(models.User).filter(models.User.id == user_id).with_for_update().one()


def _replay(db: Session, user_id: str, key: str, fingerprint: str):
    prior = db.query(models.PaymentRequest).filter(
        models.PaymentRequest.user_id == user_id, models.PaymentRequest.key == key,
    ).first()
    if prior is None:
        return None
    if prior.fingerprint != fingerprint:
        raise HTTPException(status_code=409, detail="This Idempotency-Key was already used for a different request.")
    result = json.loads(prior.response_json)
    db.commit()
    return result


def _persist(db: Session, user_id: str, key: str, fingerprint: str, response) -> None:
    db.add(models.PaymentRequest(
        user_id=user_id, key=key, fingerprint=fingerprint,
        response_json=json.dumps(response, separators=(",", ":")),
    ))
    db.commit()


def _active(account: models.Account, label: str = "Account") -> None:
    if account.status != models.AccountStatus.active:
        raise HTTPException(status_code=400, detail=f"{label} is not active.")


def _balance_limit(minor: int) -> None:
    if minor > MAX_MINOR:
        raise HTTPException(status_code=400, detail="This operation would exceed the maximum account balance.")


def _money_operation(
    operation: str, account_id: str, payload: schemas.DepositWithdraw,
    db: Session, user: models.User, idempotency_key: str | None,
):
    user_id = user.id
    key = _key(idempotency_key)
    amount_minor = parse_minor(payload.amount)
    fingerprint = _fingerprint(operation, [account_id], amount_minor, payload.note)
    try:
        _begin(db, user_id)
        previous = _replay(db, user_id, key, fingerprint)
        if previous is not None:
            return previous
        owned_account(db, account_id, user)
        account = db.query(models.Account).filter(models.Account.id == account_id).populate_existing().with_for_update().one()
        _active(account)
        if operation == "withdraw" and account.balance_minor < amount_minor:
            raise HTTPException(status_code=400, detail="Insufficient funds.")
        new_balance = account.balance_minor + amount_minor if operation == "deposit" else account.balance_minor - amount_minor
        _balance_limit(new_balance)
        account.balance_minor = new_balance
        txn = models.Transaction(
            account_id=account.id,
            type=models.TransactionType.deposit if operation == "deposit" else models.TransactionType.withdrawal,
            amount_minor=amount_minor, balance_after_minor=new_balance, note=payload.note,
        )
        db.add(txn)
        db.flush()
        response = schemas.TransactionOut.model_validate(txn).model_dump(mode="json")
        _persist(db, user_id, key, fingerprint, response)
        return response
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The payment could not be completed. Retry using the same Idempotency-Key.")
    except Exception:
        db.rollback()
        raise


@router.post("/accounts/{account_id}/deposit", response_model=schemas.TransactionOut)
def deposit(
    account_id: str, payload: schemas.DepositWithdraw,
    db: Session = Depends(get_db), user: models.User = Depends(require_customer),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    return _money_operation("deposit", account_id, payload, db, user, idempotency_key)


@router.post("/accounts/{account_id}/withdraw", response_model=schemas.TransactionOut)
def withdraw(
    account_id: str, payload: schemas.DepositWithdraw,
    db: Session = Depends(get_db), user: models.User = Depends(require_customer),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    return _money_operation("withdraw", account_id, payload, db, user, idempotency_key)


@router.post("/transfer", response_model=list[schemas.TransactionOut])
def transfer(
    payload: schemas.TransferRequest,
    db: Session = Depends(get_db), user: models.User = Depends(require_customer),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    user_id = user.id
    key = _key(idempotency_key)
    amount_minor = parse_minor(payload.amount)
    if payload.from_account_id == payload.to_account_id:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account.")
    fingerprint = _fingerprint("transfer", [payload.from_account_id, payload.to_account_id], amount_minor, payload.note)
    try:
        _begin(db, user_id)
        previous = _replay(db, user_id, key, fingerprint)
        if previous is not None:
            return previous
        owned_account(db, payload.from_account_id, user)
        # Transfers lock account rows in a consistent order on PostgreSQL.
        accounts = db.query(models.Account).filter(
            models.Account.id.in_([payload.from_account_id, payload.to_account_id])
        ).order_by(models.Account.id).populate_existing().with_for_update().all()
        by_id = {account.id: account for account in accounts}
        if payload.to_account_id not in by_id:
            raise HTTPException(status_code=404, detail="Destination account not found.")
        from_acc, to_acc = by_id[payload.from_account_id], by_id[payload.to_account_id]
        _active(from_acc, "Source account")
        _active(to_acc, "Destination account")
        if from_acc.balance_minor < amount_minor:
            raise HTTPException(status_code=400, detail="Insufficient funds.")
        _balance_limit(to_acc.balance_minor + amount_minor)
        from_acc.balance_minor -= amount_minor
        to_acc.balance_minor += amount_minor
        out_txn = models.Transaction(
            account_id=from_acc.id, type=models.TransactionType.transfer_out,
            amount_minor=amount_minor, balance_after_minor=from_acc.balance_minor,
            related_account_id=to_acc.id, note=payload.note,
        )
        in_txn = models.Transaction(
            account_id=to_acc.id, type=models.TransactionType.transfer_in,
            amount_minor=amount_minor, balance_after_minor=to_acc.balance_minor,
            related_account_id=from_acc.id, note=payload.note,
        )
        db.add_all([out_txn, in_txn])
        db.flush()
        # Only expose the sender's receipt, never the recipient's balance.
        response = [schemas.TransactionOut.model_validate(out_txn).model_dump(mode="json")]
        _persist(db, user_id, key, fingerprint, response)
        return response
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="The payment could not be completed. Retry using the same Idempotency-Key.")
    except Exception:
        db.rollback()
        raise


@router.get("/accounts/{account_id}/transactions", response_model=list[schemas.TransactionOut])
def transaction_history(
    account_id: str, db: Session = Depends(get_db), user: models.User = Depends(require_customer),
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    transaction_type: models.TransactionType | None = Query(None, alias="type"),
    from_date: datetime | None = None, to_date: datetime | None = None,
):
    owned_account(db, account_id, user)
    def utc_naive(value: datetime) -> datetime:
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if from_date and to_date and utc_naive(from_date) > utc_naive(to_date):
        raise HTTPException(status_code=422, detail="from_date must be earlier than or equal to to_date.")
    query = db.query(models.Transaction).filter(models.Transaction.account_id == account_id)
    if transaction_type is not None:
        query = query.filter(models.Transaction.type == transaction_type)
    if from_date is not None:
        query = query.filter(models.Transaction.created_at >= utc_naive(from_date))
    if to_date is not None:
        query = query.filter(models.Transaction.created_at <= utc_naive(to_date))
    return query.order_by(models.Transaction.created_at.desc(), models.Transaction.id.desc()).offset(offset).limit(limit).all()

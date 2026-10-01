from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .. import models, schemas
from ..auth import owned_account, require_customer
from ..database import get_db
from ..money import parse_minor

router = APIRouter(prefix="/accounts", tags=["Accounts"])


@router.post("/", response_model=schemas.AccountOut, status_code=201)
def create_account(
    payload: schemas.AccountCreate, db: Session = Depends(get_db),
    user: models.User = Depends(require_customer),
):
    user_id = user.id
    if str(payload.email).casefold() != user.email.casefold():
        raise HTTPException(status_code=403, detail="Use your registered email address.")
    if " ".join(payload.owner_name.split()).casefold() != " ".join(user.full_name.split()).casefold():
        raise HTTPException(status_code=403, detail="Use your registered full name.")
    opening_minor = parse_minor(payload.opening_deposit)
    try:
        # Finish the auth read before acquiring a SQLite writer reservation.
        db.commit()
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        else:
            db.query(models.User).filter(models.User.id == user_id).with_for_update().one()
        existing = db.query(models.Account).filter(models.Account.email == str(payload.email).casefold()).first()
        if existing:
            raise HTTPException(status_code=400, detail="An account with this email already exists.")
        account = models.Account(
            owner_name=payload.owner_name.strip(), email=str(payload.email).casefold(),
            owner_user_id=user_id, balance_minor=opening_minor,
        )
        db.add(account)
        db.flush()
        if opening_minor:
            db.add(models.Transaction(
                account_id=account.id, type=models.TransactionType.deposit,
                amount_minor=opening_minor, balance_after_minor=opening_minor,
                note="Opening deposit",
            ))
        # The account and opening ledger entry succeed or fail together.
        db.commit()
        db.refresh(account)
        return account
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    except Exception:
        db.rollback()
        raise


@router.get("/{account_id}", response_model=schemas.AccountOut)
def get_account(
    account_id: str, db: Session = Depends(get_db),
    user: models.User = Depends(require_customer),
):
    return owned_account(db, account_id, user)


@router.get("/", response_model=list[schemas.AccountOut])
def list_accounts(db: Session = Depends(get_db), user: models.User = Depends(require_customer)):
    return (db.query(models.Account).filter(models.Account.owner_user_id == user.id)
            .order_by(models.Account.created_at, models.Account.id).all())

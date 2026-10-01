"""Explicit, idempotent synthetic customer/reviewer fixtures. Never resets users."""
from .auth import demo_mode, hash_password
from .database import SessionLocal, initialize_database
from . import models

PASSWORD = 'VaultDemo!2026'
FIXTURES = (
    ('Ayesha Khan', 'ayesha.customer@example.com', models.UserRole.customer, 1_250_000),
    ('Hassan Ali', 'hassan.customer@example.com', models.UserRole.customer, 250_000),
    ('Demo Reviewer', 'reviewer@example.com', models.UserRole.reviewer, None),
)


def seed():
    if not demo_mode():
        raise SystemExit('Demo seeding is disabled. Set DEMO_MODE=true only for synthetic demo data.')
    initialize_database()
    with SessionLocal() as db:
        for name, email, role, balance_minor in FIXTURES:
            user = db.query(models.User).filter(models.User.email == email).first()
            if user is None:
                user = models.User(full_name=name, email=email, role=role, password_hash=hash_password(PASSWORD))
                db.add(user)
                db.flush()
            # Do not claim legacy records or attach a fixture to a differently configured user.
            if user.full_name != name or user.role != role:
                raise RuntimeError(f'Demo fixture conflicts with an existing user: {email}')
            if balance_minor is not None:
                account = db.query(models.Account).filter(models.Account.email == email).first()
                if account is None:
                    account = models.Account(owner_user_id=user.id, owner_name=name, email=email, balance_minor=balance_minor)
                    db.add(account)
                    db.flush()
                    db.add(models.Transaction(account_id=account.id, type=models.TransactionType.deposit, amount_minor=balance_minor, balance_after_minor=balance_minor, note='Synthetic demo opening deposit'))
                elif account.owner_user_id != user.id:
                    raise RuntimeError(f'Demo fixture conflicts with an existing account: {email}')
        db.commit()
    print('Synthetic demo users prepared. Credentials are in README.md; no existing password or balance was reset.')


if __name__ == '__main__':
    seed()

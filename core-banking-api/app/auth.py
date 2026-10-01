"""Password hashing and revocable opaque sessions; role and ownership checks."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from . import models
from .database import get_db

COOKIE_NAME = 'vault_session'
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS).hex()
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# Equal work for unknown email and incorrect password.
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


def demo_mode():
    return os.getenv('DEMO_MODE', 'true').lower() == 'true'


def session_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db: Session, user: models.User, response: Response):
    token = secrets.token_urlsafe(32)
    db.add(models.AuthSession(user_id=user.id, token_hash=session_hash(token), expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS)))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, samesite='strict', secure=os.getenv('COOKIE_SECURE', 'false').lower() == 'true', path='/')


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    header = request.headers.get('authorization', '')
    token = header[7:].strip() if header.lower().startswith('bearer ') else request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 256:
        raise HTTPException(401, 'Please sign in.', headers={'WWW-Authenticate': 'Bearer'})
    session = db.query(models.AuthSession).filter(models.AuthSession.token_hash == session_hash(token)).first()
    current_time = datetime.now(timezone.utc).replace(tzinfo=None)
    if session is None or session.expires_at.replace(tzinfo=None) <= current_time:
        raise HTTPException(401, 'Your session has expired. Please sign in.')
    user = session.user
    if user is None:
        raise HTTPException(401, 'Please sign in.')
    return user


def require_customer(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.customer:
        raise HTTPException(403, 'A customer account is required.')
    return user


def require_reviewer(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != models.UserRole.reviewer:
        raise HTTPException(403, 'Reviewer permission is required.')
    return user


def owned_account(db: Session, account_id: str, user: models.User):
    account = db.query(models.Account).filter(models.Account.id == account_id, models.Account.owner_user_id == user.id).first()
    if account is None:
        raise HTTPException(404, 'Account not found.')
    return account

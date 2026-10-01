import threading
import time
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .. import models, schemas
from ..auth import COOKIE_NAME, DUMMY_PASSWORD_HASH, demo_mode, get_current_user, hash_password, issue_session, session_hash, verify_password
from ..database import get_db

router = APIRouter(prefix='/auth', tags=['Authentication'])
_attempts = OrderedDict()
_attempts_lock = threading.Lock()


def throttle(request: Request):
    # A bounded, per-process login throttle; a shared gateway is needed for multiple workers.
    client = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with _attempts_lock:
        recent = [stamp for stamp in _attempts.pop(client, []) if now - stamp < 60]
        if len(recent) >= 30:
            _attempts[client] = recent
            raise HTTPException(429, 'Too many sign-in attempts. Try again in a minute.')
        recent.append(now)
        _attempts[client] = recent
        while len(_attempts) > 2000:
            _attempts.popitem(last=False)


@router.get('/config')
def config():
    return {'demo_mode': demo_mode()}


@router.post('/register', response_model=schemas.AuthOut, status_code=201)
def register(payload: schemas.RegisterRequest, response: Response, request: Request, db: Session = Depends(get_db)):
    throttle(request)
    email = str(payload.email).lower()
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, 'This email is already registered.')
    user = models.User(full_name=payload.full_name, email=email, password_hash=hash_password(payload.password), role=models.UserRole.customer)
    try:
        db.add(user)
        db.flush()
        issue_session(db, user, response)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'This email is already registered.')
    return {'user': user}


@router.post('/login', response_model=schemas.AuthOut)
def login(payload: schemas.LoginRequest, response: Response, request: Request, db: Session = Depends(get_db)):
    throttle(request)
    user = db.query(models.User).filter(models.User.email == str(payload.email).lower()).first()
    valid = verify_password(payload.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if not user or not valid:
        raise HTTPException(401, 'Email or password is incorrect.')
    # Rotate a caller's previous session on sign-in.
    previous = request.cookies.get(COOKIE_NAME)
    if previous:
        db.query(models.AuthSession).filter(models.AuthSession.token_hash == session_hash(previous)).delete()
    issue_session(db, user, response)
    db.commit()
    return {'user': user}


@router.get('/me', response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@router.post('/logout', status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    header = request.headers.get('authorization', '')
    if header.lower().startswith('bearer '):
        token = header[7:].strip()
    if token:
        db.query(models.AuthSession).filter(models.AuthSession.token_hash == session_hash(token)).delete()
        db.commit()
    response.delete_cookie(COOKIE_NAME, path='/')

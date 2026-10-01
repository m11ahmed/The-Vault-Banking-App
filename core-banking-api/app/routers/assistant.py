from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..assistant_service import AssistantStatus, ChatRequest, ChatResponse, assistant_status, chat
from ..database import get_db
from .. import models
from ..auth import require_customer, owned_account

router = APIRouter(prefix="/assistant", tags=["Banking Assistant"])


@router.get("/status", response_model=AssistantStatus)
def status():
    return assistant_status()


@router.post("/chat", response_model=ChatResponse)
def assistant_chat(payload: ChatRequest, db: Session = Depends(get_db), user: models.User = Depends(require_customer)):
    if payload.account_id:
        owned_account(db, payload.account_id, user)
    return chat(payload, db)

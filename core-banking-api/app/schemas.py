"""API money is a two-decimal string; storage uses integer paisa."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, PlainSerializer, field_validator
from .models import AccountStatus, TransactionType, KYCStatus, UserRole

def validate_money(value: Decimal):
    from .money import parse_minor
    parse_minor(value)
    return value.quantize(Decimal("0.01"))

Money = Annotated[Decimal, AfterValidator(validate_money), PlainSerializer(lambda v: format(v, '.2f'), return_type=str, when_used='json')]

class ORMOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    @field_validator('full_name')
    @classmethod
    def name(cls, value):
        value = ' '.join(value.split())
        if len(value) < 2:
            raise ValueError('Name must contain at least two characters.')
        return value

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

class UserOut(ORMOut):
    id: str
    full_name: str
    email: str
    role: UserRole

class AuthOut(BaseModel):
    user: UserOut

class AccountCreate(BaseModel):
    owner_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    opening_deposit: Money = Field(default=Decimal('0.00'), ge=0)

class AccountOut(ORMOut):
    id: str
    owner_name: str
    email: str
    balance: Money
    status: AccountStatus
    created_at: datetime

class DepositWithdraw(BaseModel):
    amount: Money = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)

class TransferRequest(DepositWithdraw):
    from_account_id: str = Field(min_length=1, max_length=64)
    to_account_id: str = Field(min_length=1, max_length=64)

class TransactionOut(ORMOut):
    id: str
    account_id: str
    type: TransactionType
    amount: Money
    balance_after: Money
    related_account_id: str | None = None
    note: str | None = None
    created_at: datetime

class KYCSubmit(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    id_number: str = Field(min_length=5, max_length=50)
    document_type: str = Field(min_length=2, max_length=50)
    @field_validator('full_name', 'id_number', 'document_type', mode='before')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError('Field is too short.')
        return value

class KYCReview(BaseModel):
    approve: bool
    reason: str = Field(min_length=3, max_length=500)
    expected_submitted_at: datetime
    @field_validator('reason')
    @classmethod
    def reason_nonblank(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError('Provide a review reason of at least three characters.')
        return value

class KYCOut(ORMOut):
    id: str
    account_id: str
    owner_name: str | None = None
    full_name: str
    id_number: str
    document_type: str
    status: KYCStatus
    submitted_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_reason: str | None = None

class KYCReviewAuditOut(ORMOut):
    id: str
    record_id: str
    reviewer_id: str
    decision: str
    reason: str
    created_at: datetime

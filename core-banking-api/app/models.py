"""Persisted banking records, exact paisa amounts, sessions and review decisions."""
import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base

def gen_id():
    return str(uuid.uuid4())

def now():
    return datetime.now(timezone.utc)

class UserRole(str, enum.Enum):
    customer = "customer"
    reviewer = "reviewer"

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=gen_id)
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.customer)
    created_at = Column(DateTime, nullable=False, default=now)

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)
    user = relationship("User")

class AccountStatus(str, enum.Enum):
    active = "active"
    frozen = "frozen"
    closed = "closed"

class KYCStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"

def money_property(column):
    def get(instance):
        return (Decimal(getattr(instance, column) or 0) / 100).quantize(Decimal("0.01"))
    def set_value(instance, value):
        from .money import parse_minor
        setattr(instance, column, parse_minor(Decimal(str(value))))
    return property(get, set_value)

class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance_minor >= 0 AND balance_minor <= 100000000000", name="valid_balance"),)
    id = Column(String, primary_key=True, default=gen_id)
    owner_user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    owner_name = Column(String, nullable=False)
    email = Column(String, nullable=False, unique=True)
    balance_minor = Column(BigInteger, nullable=False, default=0)
    balance = money_property("balance_minor")
    status = Column(Enum(AccountStatus), nullable=False, default=AccountStatus.active)
    created_at = Column(DateTime, nullable=False, default=now)
    transactions = relationship("Transaction", back_populates="account", cascade="all, delete-orphan", foreign_keys="Transaction.account_id")
    kyc_record = relationship("KYCRecord", back_populates="account", uselist=False, cascade="all, delete-orphan")

class TransactionType(str, enum.Enum):
    deposit = "deposit"
    withdrawal = "withdrawal"
    transfer_in = "transfer_in"
    transfer_out = "transfer_out"

class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (CheckConstraint("amount_minor > 0 AND amount_minor <= 100000000000", name="valid_amount"), CheckConstraint("balance_after_minor >= 0 AND balance_after_minor <= 100000000000", name="valid_balance_after"))
    id = Column(String, primary_key=True, default=gen_id)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, index=True)
    type = Column(Enum(TransactionType), nullable=False)
    amount_minor = Column(BigInteger, nullable=False)
    balance_after_minor = Column(BigInteger, nullable=False)
    amount = money_property("amount_minor")
    balance_after = money_property("balance_after_minor")
    related_account_id = Column(String, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now, index=True)
    account = relationship("Account", back_populates="transactions", foreign_keys=[account_id])

class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    __table_args__ = (UniqueConstraint("user_id", "key", name="user_payment_key"),)
    id = Column(String, primary_key=True, default=gen_id)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    key = Column(String(100), nullable=False)
    fingerprint = Column(String, nullable=False)
    response_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)

class KYCRecord(Base):
    __tablename__ = "kyc_records"
    id = Column(String, primary_key=True, default=gen_id)
    account_id = Column(String, ForeignKey("accounts.id"), nullable=False, unique=True)
    full_name = Column(String, nullable=False)
    id_number = Column(String, nullable=False)
    document_type = Column(String, nullable=False)
    status = Column(Enum(KYCStatus), nullable=False, default=KYCStatus.pending)
    submitted_at = Column(DateTime, nullable=False, default=now)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, ForeignKey("users.id"), nullable=True)
    review_reason = Column(String, nullable=True)
    account = relationship("Account", back_populates="kyc_record")

class KYCReviewAudit(Base):
    __tablename__ = "kyc_review_audit"
    id = Column(String, primary_key=True, default=gen_id)
    record_id = Column(String, ForeignKey("kyc_records.id"), nullable=False, index=True)
    reviewer_id = Column(String, ForeignKey("users.id"), nullable=False)
    decision = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)

class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version = Column(BigInteger, primary_key=True)

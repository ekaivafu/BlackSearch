import datetime
from sqlalchemy import BigInteger, Column, Integer, String, DateTime, ForeignKey, Enum, JSON, Boolean, text
from sqlalchemy.sql import func
from .base import Base
import enum

class PlanType(str, enum.Enum):
    CREDITS = "credits"
    DAYS = "days"

class UserStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISABLED = "disabled"

class RechargeStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class TransactionType(str, enum.Enum):
    INITIAL_GRANT = "initial_grant"
    SEARCH = "search"
    RECHARGE = "recharge"
    ADMIN_ADJUSTMENT = "admin_adjustment"

class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    plan_type = Column(Enum(PlanType), nullable=False)
    credits = Column(Integer, default=0, nullable=False)
    days = Column(Integer, default=0, nullable=False)
    price = Column(Integer, default=0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class BotSetting(Base):
    __tablename__ = "bot_settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class RequiredChannel(Base):
    __tablename__ = "required_channels"

    id = Column(Integer, primary_key=True, index=True)
    channel_id = Column(BigInteger, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    username = Column(String, nullable=True)
    invite_link = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    status = Column(Enum(UserStatus), default=UserStatus.PENDING, nullable=False)
    credits = Column(Integer, default=0, nullable=False)
    total_searches = Column(Integer, default=0, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(BigInteger, nullable=True)
    subscription_end = Column(DateTime(timezone=True), nullable=True)

class RechargeRequest(Base):
    __tablename__ = "recharge_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    requested_credits = Column(Integer, nullable=False)
    status = Column(Enum(RechargeStatus), default=RechargeStatus.PENDING, nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True)
    
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    processed_at = Column(DateTime(timezone=True), nullable=True)
    processed_by = Column(BigInteger, nullable=True)

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Integer, nullable=False)  # can be negative for deduction
    transaction_type = Column(Enum(TransactionType), nullable=False)
    reference_id = Column(String, nullable=True)  # Can store search_log_id or recharge_request_id
    balance_after = Column(Integer, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(BigInteger, nullable=True)  # Admin ID if adjusted, else None or User ID

class SearchLog(Base):
    __tablename__ = "search_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    query_metadata = Column(JSON, nullable=False)  # Don't store full PII, just metadata (e.g. type: phone, time)
    success = Column(Integer, nullable=False)  # 1 for success, 0 for failure
    credits_used = Column(Integer, default=1, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())

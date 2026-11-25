"""SQLAlchemy ORM models for activation/promo codes and their redemptions."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    and_,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from db.base import Base


class PromoCode(Base):
    __tablename__ = "promo_codes"
    promo_code_id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, unique=True, nullable=False, index=True)
    bonus_days = Column(Integer, nullable=False)
    regular_traffic_gb = Column(Numeric(12, 3), nullable=True)
    premium_traffic_gb = Column(Numeric(12, 3), nullable=True)
    discount_percent = Column(Numeric(5, 2), nullable=True)
    duration_multiplier = Column(Numeric(6, 3), nullable=True)
    traffic_multiplier = Column(Numeric(6, 3), nullable=True)
    bonus_requires_payment = Column(Boolean, nullable=False, default=False)
    applies_to = Column(String(32), nullable=False, default="all")
    min_subscription_months = Column(Integer, nullable=True)
    min_subscription_days = Column(Integer, nullable=True)
    subscription_tariff_key = Column(String, nullable=True)
    exact_subscription_days = Column(Integer, nullable=True)
    min_traffic_gb = Column(Numeric(10, 2), nullable=True)
    origin = Column(String(32), nullable=False, default="admin")
    owner_plugin_id = Column(String(64), nullable=True)
    # Set only for a code minted for one customer; NULL means a shared code.
    user_id = Column(BigInteger, ForeignKey("users.user_id"), nullable=True, index=True)
    max_activations = Column(Integer, nullable=False)
    current_activations = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_by_admin_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    valid_until = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_code = Column(String, nullable=True)

    activations = relationship(
        "PromoCodeActivation", back_populates="promo_code", cascade="all, delete-orphan"
    )
    payments_where_used = relationship("Payment", back_populates="promo_code_used")


class PromoCodeActivation(Base):
    __tablename__ = "promo_code_activations"

    activation_id = Column(Integer, primary_key=True, autoincrement=True)
    promo_code_id = Column(Integer, ForeignKey("promo_codes.promo_code_id"), nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.user_id"), nullable=False)
    activated_at = Column(DateTime(timezone=True), server_default=func.now())
    payment_id = Column(Integer, ForeignKey("payments.payment_id"), nullable=True)
    effect_summary = Column(String, nullable=True)
    bonus_days = Column(Integer, nullable=True)
    regular_traffic_gb = Column(Numeric(12, 3), nullable=True)
    premium_traffic_gb = Column(Numeric(12, 3), nullable=True)
    discount_percent = Column(Numeric(5, 2), nullable=True)
    duration_multiplier = Column(Numeric(6, 3), nullable=True)
    traffic_multiplier = Column(Numeric(6, 3), nullable=True)
    applies_to = Column(String(32), nullable=True)
    base_amount = Column(Float, nullable=True)
    discount_amount = Column(Float, nullable=True)
    charged_months = Column(Integer, nullable=True)
    charged_days = Column(Integer, nullable=True)
    charged_gb = Column(Float, nullable=True)
    granted_days = Column(Integer, nullable=True)
    granted_gb = Column(Float, nullable=True)
    granted_regular_traffic_gb = Column(Numeric(12, 3), nullable=True)
    granted_premium_traffic_gb = Column(Numeric(12, 3), nullable=True)
    is_manual_override = Column(Boolean, nullable=False, default=False)
    # A historical standard redemption retained beside another account's
    # standard redemption at merge. This is provenance, not a new grant or a
    # manual override. The deleted account ID deliberately has no foreign key.
    merged_from_user_id = Column(BigInteger, nullable=True)

    promo_code = relationship("PromoCode", back_populates="activations")
    user = relationship("User", back_populates="promo_code_activations")
    payment = relationship("Payment")

    __table_args__ = (
        Index(
            "uq_promo_user_activation_standard",
            "promo_code_id",
            "user_id",
            unique=True,
            postgresql_where=and_(is_manual_override.is_(False), merged_from_user_id.is_(None)),
            sqlite_where=and_(is_manual_override.is_(False), merged_from_user_id.is_(None)),
        ),
    )

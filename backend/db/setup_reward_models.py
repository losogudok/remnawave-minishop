"""Durable, account-level setup completion and reward grant state."""

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from db.base import Base


class SetupReward(Base):
    __tablename__ = "setup_rewards"
    __table_args__ = (UniqueConstraint("user_id", "reward_type", name="uq_setup_reward_user_type"),)

    id = Column(String(32), primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    reward_type = Column(String(16), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    accepted_days = Column(Integer, nullable=True)
    subscription_id = Column(Integer, nullable=True)
    target_end_date = Column(DateTime(timezone=True), nullable=True)
    confirmed_end_date = Column(DateTime(timezone=True), nullable=True)
    state = Column(String(16), nullable=False, default="completed")
    history_json = Column(Text, nullable=True)

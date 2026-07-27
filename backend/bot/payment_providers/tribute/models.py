from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TributeSubscriptionPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subscription_name: str = Field(min_length=1, max_length=512)
    subscription_id: int = Field(gt=0)
    period_id: int = Field(gt=0)
    period: str = Field(min_length=1, max_length=64)
    price: int = Field(ge=0)
    amount: int = Field(ge=0)
    currency: str = Field(min_length=1, max_length=16)
    user_id: int | None = None
    trb_user_id: str | None = Field(default=None, min_length=1, max_length=128)
    telegram_user_id: int | None = Field(default=None, gt=0)
    telegram_username: str | None = Field(default=None, max_length=128)
    channel_id: int
    channel_name: str = Field(min_length=1, max_length=512)
    expires_at: datetime
    type: str | None = Field(default=None, max_length=32)
    cancel_reason: str | None = Field(default=None, max_length=1024)
    email: str | None = Field(default=None, max_length=320)
    web_app_link: str | None = Field(default=None, max_length=2048)

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalnum():
            raise ValueError("currency must be alphanumeric")
        return normalized

    @field_validator("subscription_name", "period", "channel_name")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("trb_user_id")
    @classmethod
    def _strip_optional_identity(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @field_validator("type")
    @classmethod
    def _normalize_type(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip().lower()
        return normalized or None


class TributeDigitalProductPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_id: int = Field(gt=0)
    product_name: str = Field(min_length=1, max_length=512)
    amount: int = Field(ge=0)
    currency: str = Field(min_length=1, max_length=16)
    purchase_id: int = Field(gt=0)
    transaction_id: int = Field(gt=0)
    purchase_created_at: datetime
    user_id: int | None = None
    trb_user_id: str | None = Field(default=None, max_length=128)
    telegram_user_id: int | None = Field(default=None, gt=0)
    telegram_username: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalnum():
            raise ValueError("currency must be alphanumeric")
        return normalized

    @field_validator("product_name")
    @classmethod
    def _strip_product_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("product_name must not be empty")
        return normalized

    @field_validator("trb_user_id")
    @classmethod
    def _strip_trb_user_id(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class TributeDigitalProductRefundPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    product_id: int = Field(gt=0)
    product_name: str = Field(default="", max_length=512)
    amount: int = Field(ge=0)
    currency: str = Field(min_length=1, max_length=16)
    purchase_id: int = Field(gt=0)
    transaction_id: int = Field(gt=0)
    refund_reason: str | None = Field(default=None, max_length=512)
    refunded_at: datetime
    user_id: int | None = None
    trb_user_id: str | None = Field(default=None, max_length=128)
    telegram_user_id: int | None = Field(default=None, gt=0)
    telegram_username: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=320)

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalnum():
            raise ValueError("currency must be alphanumeric")
        return normalized

    @field_validator("trb_user_id")
    @classmethod
    def _strip_trb_user_id(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class TributeWebhookEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=64)
    created_at: datetime
    sent_at: datetime
    payload: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return value.strip().lower()

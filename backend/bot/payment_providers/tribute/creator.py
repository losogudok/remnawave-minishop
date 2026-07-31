"""Typed Tribute Creator API contracts for admin-side catalog discovery.

The Creator API is the only place that exposes ``subscription_id``,
``period_id`` and ``product_id``: the seller dashboard shows a share link and
nothing else, and the link carries no numeric identity.  Reading the catalog
lets the admin panel fill the Creator fallback bindings in and compare Tribute's
own price against the local one, instead of asking an operator to transcribe
identifiers by hand.

Responses stay forward-compatible: unknown fields are accepted, and an entry
Minishop cannot model is skipped with a warning rather than failing the whole
catalog.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any, Literal

from aiohttp import ClientSession
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from .shop import (
    TRIBUTE_SHOP_API_BASE_URL,
    TRIBUTE_SHOP_PERIOD_TO_MONTHS,
    _normalize_lower_token,
    _parse_finite_decimal,
    _tribute_payment_url,
)

if TYPE_CHECKING:
    from .config import TributeConfig

logger = logging.getLogger(__name__)

# Shop and Creator methods share one base URL and one API key.
TRIBUTE_CREATOR_API_BASE_URL = TRIBUTE_SHOP_API_BASE_URL
TRIBUTE_CREATOR_PRODUCTS_PAGE_SIZE = 100
TRIBUTE_CREATOR_PRODUCTS_MAX_PAGES = 20
# Creator periods cover more than the recurring durations Minishop can sell, so
# the lookup is keyed by plain strings and simply misses the unsupported ones.
TRIBUTE_CREATOR_PERIOD_TO_MONTHS: Mapping[str, int] = MappingProxyType(
    {str(period): months for period, months in TRIBUTE_SHOP_PERIOD_TO_MONTHS.items()}
)
# Every currency Tribute settles in uses two fractional digits.
_PRODUCT_MINOR_UNITS = Decimal(100)

type TributeCreatorPeriod = Annotated[
    Literal["trial", "onetime", "weekly", "monthly", "quarterly", "halfyearly", "yearly"],
    BeforeValidator(_normalize_lower_token),
]
type TributeCreatorDecimal = Annotated[Decimal, BeforeValidator(_parse_finite_decimal)]


def _optional_creator_link(value: object) -> str | None:
    """Keep a malformed link out of the catalog without dropping the entry."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return _tribute_payment_url(normalized)
    except ValueError:
        logger.warning("Tribute Creator API returned an unusable link: %s", normalized)
        return None


class TributeCreatorApiError(RuntimeError):
    """Creator API call that did not return a usable catalog."""

    def __init__(self, code: str, *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class _TributeCreatorWireModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        strict=True,
        validate_default=True,
    )


class TributeCreatorSubscriptionPeriod(_TributeCreatorWireModel):
    """One billing period of a published Creator subscription."""

    period_id: StrictInt = Field(alias="periodId", gt=0)
    period: TributeCreatorPeriod
    price: TributeCreatorDecimal = Field(ge=0)

    @property
    def months(self) -> int | None:
        """Local tariff duration this period maps to, when Minishop sells it."""

        return TRIBUTE_CREATOR_PERIOD_TO_MONTHS.get(self.period)


class TributeCreatorSubscription(_TributeCreatorWireModel):
    """Published Creator subscription with its pricing periods."""

    subscription_id: StrictInt = Field(alias="subscriptionId", gt=0)
    name: StrictStr = Field(default="", max_length=512)
    currency: StrictStr = Field(default="")
    periods: list[TributeCreatorSubscriptionPeriod] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("currency")
    @classmethod
    def _normalize_currency(cls, value: str) -> str:
        return value.strip().lower()


class TributeCreatorProduct(_TributeCreatorWireModel):
    """Digital Product row returned by ``GET /products``."""

    id: StrictInt = Field(gt=0)
    name: StrictStr = Field(default="", max_length=512)
    type: StrictStr = Field(default="")
    status: StrictStr = Field(default="")
    amount: StrictInt = Field(default=0, ge=0)
    currency: StrictStr = Field(default="")
    link: StrictStr | None = Field(default=None)
    web_link: StrictStr | None = Field(default=None, alias="webLink")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("type", "status", "currency")
    @classmethod
    def _normalize_token(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("link", "web_link", mode="before")
    @classmethod
    def _validate_link(cls, value: object) -> str | None:
        return _optional_creator_link(value)

    @property
    def price(self) -> Decimal:
        """Product price in major units; Tribute reports it in minor ones."""

        return Decimal(self.amount) / _PRODUCT_MINOR_UNITS

    @property
    def checkout_link(self) -> str | None:
        return self.link or self.web_link


@dataclass(frozen=True, slots=True)
class TributeCreatorCatalog:
    """Everything the tariff editor needs to bind the Creator fallback."""

    subscriptions: tuple[TributeCreatorSubscription, ...] = ()
    products: tuple[TributeCreatorProduct, ...] = ()


async def _get_creator_json(
    session: ClientSession,
    path: str,
    *,
    api_key: str,
    params: Mapping[str, str] | None = None,
) -> Any:
    url = f"{TRIBUTE_CREATOR_API_BASE_URL}{path}"
    try:
        async with session.get(
            url,
            headers={"Api-Key": api_key},
            params=dict(params) if params else None,
        ) as response:
            status = response.status
            response_text = await response.text()
    except Exception as exc:
        logger.warning("Tribute Creator API request failed (%s): %s", path, exc)
        raise TributeCreatorApiError("request_failed") from exc
    if status in {401, 403}:
        raise TributeCreatorApiError("unauthorized", status=status)
    if status == 429:
        raise TributeCreatorApiError("rate_limited", status=status)
    if status != 200:
        logger.error("Tribute Creator API returned status %s for %s.", status, path)
        raise TributeCreatorApiError("request_failed", status=status)
    try:
        return json.loads(response_text) if response_text else {}
    except json.JSONDecodeError as exc:
        logger.error("Tribute Creator API returned invalid JSON for %s.", path)
        raise TributeCreatorApiError("invalid_response", status=status) from exc


def _rows_from_payload(payload: Any, key: str) -> Sequence[Any]:
    rows = payload.get(key) if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        raise TributeCreatorApiError("invalid_response")
    return rows


def _parse_rows[WireModelT: BaseModel](
    rows: Sequence[Any],
    model: type[WireModelT],
    *,
    label: str,
) -> list[WireModelT]:
    parsed: list[WireModelT] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            parsed.append(model.model_validate(dict(row)))
        except ValidationError as exc:
            logger.warning("Skipping an unsupported Tribute %s entry: %s", label, exc)
    return parsed


async def fetch_creator_subscriptions(
    session: ClientSession,
    api_key: str,
) -> list[TributeCreatorSubscription]:
    """Read every published Creator subscription with its pricing periods."""

    payload = await _get_creator_json(session, "/subscriptions", api_key=api_key)
    rows = _rows_from_payload(payload, "result")
    return _parse_rows(rows, TributeCreatorSubscription, label="subscription")


async def fetch_creator_products(
    session: ClientSession,
    api_key: str,
) -> list[TributeCreatorProduct]:
    """Read every published Creator product, following Tribute's pagination."""

    products: list[TributeCreatorProduct] = []
    for page in range(1, TRIBUTE_CREATOR_PRODUCTS_MAX_PAGES + 1):
        payload = await _get_creator_json(
            session,
            "/products",
            api_key=api_key,
            params={"page": str(page), "size": str(TRIBUTE_CREATOR_PRODUCTS_PAGE_SIZE)},
        )
        rows = _rows_from_payload(payload, "rows")
        products.extend(_parse_rows(rows, TributeCreatorProduct, label="product"))
        if len(rows) < TRIBUTE_CREATOR_PRODUCTS_PAGE_SIZE:
            break
    return products


class TributeCreatorCatalogMixin:
    """Read-only Creator catalog access used by the admin tariff editor.

    The bindings it discovers are configuration, never a payment path, so the
    mixin deliberately exposes nothing that could create or settle an order.
    """

    if TYPE_CHECKING:
        config: TributeConfig

        @property
        def configured(self) -> bool: ...

        async def _get_session(self) -> ClientSession: ...

    async def fetch_creator_catalog(self) -> TributeCreatorCatalog:
        api_key = str(self.config.API_KEY or "")
        if not self.configured or not api_key:
            raise TributeCreatorApiError("not_configured")
        session = await self._get_session()
        subscriptions = await fetch_creator_subscriptions(session, api_key)
        products = await fetch_creator_products(session, api_key)
        return TributeCreatorCatalog(
            subscriptions=tuple(subscriptions),
            products=tuple(products),
        )

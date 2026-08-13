from collections.abc import Mapping
from typing import Any

from ..shared import first_value


def payload_success(status: int, payload: Mapping[str, Any]) -> bool:
    if status < 200 or status >= 300:
        return False
    if "success" not in payload:
        return True
    value = payload.get("success")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "success"}


def response_error(payload: Mapping[str, Any]) -> Any:
    return payload.get("message") or payload.get("error") or payload.get("errors") or payload


def bool_form_value(value: bool | None) -> str | None:
    if value is None:
        return None
    return "1" if value else "0"


def status_value(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return ""
    return str(payload.get("status") or payload.get("Status") or "").strip().lower()


def bill_id_value(payload: Mapping[str, Any] | None) -> str | None:
    return first_value(payload, "bill_id", "billId", "id", "TrsId", "trs_id")


def payment_page_url(payload: Mapping[str, Any] | None) -> str | None:
    return first_value(
        payload,
        "link_page_url",
        "linkPageUrl",
        "page_url",
        "pageUrl",
        "transfer_url",
        "transferUrl",
        "link_url",
        "linkUrl",
        "payment_url",
        "paymentUrl",
        "url",
    )

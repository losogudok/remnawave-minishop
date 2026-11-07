"""Sanitized push payload and same-origin action URL helpers."""

from __future__ import annotations

import hashlib
import posixpath
import re
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

PUSH_TITLE_MAX_LENGTH = 120
PUSH_BODY_MAX_LENGTH = 200
PUSH_TAG_LENGTH = 32
PUSH_URL_MAX_LENGTH = 512
_ALLOWED_PATH_PREFIXES = (
    "/trial",
    "/invite",
    "/partner",
    "/devices",
    "/support",
    "/settings",
    "/notifications",
    "/security",
    "/status",
    "/install",
    "/extensions",
    "/payments",
    "/billing",
)
_SENSITIVE_QUERY_PARTS = ("token", "access_token", "secret", "code", "session")
_WHITESPACE_RE = re.compile(r"\s+")


def _compact(value: Any, limit: int) -> str:
    text = _WHITESPACE_RE.sub(" ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def sanitize_push_content(title: Any, body: Any, *, fallback_title: str) -> tuple[str, str]:
    safe_title = _compact(title, PUSH_TITLE_MAX_LENGTH) or _compact(
        fallback_title, PUSH_TITLE_MAX_LENGTH
    )
    return safe_title, _compact(body, PUSH_BODY_MAX_LENGTH)


def push_tag_for(occurrence_id: str) -> str:
    return hashlib.sha256(str(occurrence_id or "").encode("utf-8")).hexdigest()[:PUSH_TAG_LENGTH]


def _origin_for(settings: Any) -> str:
    raw = str(settings.SUBSCRIPTION_MINI_APP_URL or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _path_allowed(path: str) -> bool:
    normalized = path or "/"
    if normalized == "/":
        return True
    return any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in _ALLOWED_PATH_PREFIXES
    )


def _query_safe(query: str) -> bool:
    for key, _value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if any(part in lowered for part in _SENSITIVE_QUERY_PARTS):
            return False
    return True


def _repeatedly_unquote(value: str) -> str | None:
    """Decode until stable, rejecting ambiguous multi-encoded traversal."""

    current = value
    for _attempt in range(5):
        decoded = unquote(current)
        if decoded == current:
            return current
        current = decoded
    return None


def _canonical_path(path: str) -> str | None:
    if not path.startswith("/") or "\\" in path or "\x00" in path:
        return None
    canonical = posixpath.normpath(path)
    if not canonical.startswith("/") or canonical.startswith("//"):
        return None
    if canonical == "/.":
        return "/"
    return canonical


def normalize_push_url(raw: Any, settings: Any) -> str:
    """Return a safe same-origin Web App path or ``/``.

    External URLs (including Telegram links), token/share paths, fragment
    identifiers and sensitive query keys are never forwarded to the browser.
    Percent-encoded dot segments, encoded slashes and backslashes are resolved
    with browser-compatible rules before the share/allowlist checks run.
    """

    value = str(raw or "").strip()
    if not value:
        return "/"
    decoded = _repeatedly_unquote(value)
    if decoded is None or "\\" in decoded or "\x00" in decoded:
        return "/"
    if decoded.startswith(("//", "/\\")):
        return "/"

    if decoded.startswith("/"):
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            return "/"
        if parsed.scheme or parsed.netloc:
            return "/"
        path = parsed.path or "/"
        query = parsed.query
    else:
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            return "/"
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "/"
        origin = _origin_for(settings)
        if not origin:
            return "/"
        request_origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if request_origin != origin:
            return "/"
        path = parsed.path or "/"
        query = parsed.query

    canonical = _canonical_path(path)
    if canonical is None:
        return "/"
    if canonical == "/s" or canonical.startswith("/s/"):
        return "/"
    if not _path_allowed(canonical):
        return "/"
    if query and not _query_safe(query):
        return "/"
    # Fragments are always dropped: they are never needed for routing and can
    # carry credentials from a mistakenly pasted link.
    normalized = urlunsplit(("", "", canonical, query, ""))
    return normalized if len(normalized) <= PUSH_URL_MAX_LENGTH else "/"


def build_push_payload(delivery: Any) -> dict[str, Any]:
    """Build the service-worker JSON contract for one durable delivery."""

    payload: dict[str, Any] = {
        "title": _compact(getattr(delivery, "title", ""), PUSH_TITLE_MAX_LENGTH),
        "url": str(getattr(delivery, "url", "") or "/"),
    }
    body = _compact(getattr(delivery, "body", ""), PUSH_BODY_MAX_LENGTH)
    if body:
        payload["body"] = body
    tag = str(getattr(delivery, "tag", "") or "").strip()
    if tag:
        payload["tag"] = tag[:64]
    return payload


def encode_push_payload(delivery: Any) -> bytes:
    import json

    return json.dumps(
        build_push_payload(delivery),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def payload_varies_without_credentials(payload: dict[str, Any]) -> bool:
    """Guard against lock-screen credential leakage in tests and diagnostics."""

    text = " ".join(str(value) for value in payload.values()).lower()
    return not any(
        marker in text
        for marker in ("authorization:", "bearer ", "access_token", "password", "secret=")
    )


__all__ = [
    "PUSH_BODY_MAX_LENGTH",
    "PUSH_TAG_LENGTH",
    "PUSH_TITLE_MAX_LENGTH",
    "PUSH_URL_MAX_LENGTH",
    "build_push_payload",
    "encode_push_payload",
    "normalize_push_url",
    "payload_varies_without_credentials",
    "push_tag_for",
    "sanitize_push_content",
]

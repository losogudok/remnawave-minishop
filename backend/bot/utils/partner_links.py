"""Pure builders for partner-program deep links."""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


def build_partner_bot_link(bot_username: str | None, partner_code: str | None) -> str | None:
    username = str(bot_username or "").strip().lstrip("@")
    code = str(partner_code or "").strip()
    if not username or not code:
        return None
    return f"https://t.me/{quote(username, safe='')}?start=p_{quote(code, safe='')}"


def build_partner_webapp_link(base_url: str | None, partner_code: str | None) -> str | None:
    base = str(base_url or "").strip()
    code = str(partner_code or "").strip()
    if not base or not code:
        return None
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["partner"] = code
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path or "/",
            urlencode(query),
            parts.fragment,
        )
    )


__all__ = ["build_partner_bot_link", "build_partner_webapp_link"]

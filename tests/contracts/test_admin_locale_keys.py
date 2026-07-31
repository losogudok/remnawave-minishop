"""Every admin string the panel asks for must exist in both base locales.

The admin translator resolves ``at("key")`` as ``admin_key`` and falls back to
the English literal written at the call site when that key is missing. A
missing Russian entry is therefore invisible in review and in tests: the panel
simply shows English to a Russian admin. This guard makes that a failing test
instead of something a user reports.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"
LOCALES = REPO_ROOT / "locales"

# ``at("some_key"`` — the first argument is always a literal, because a
# computed key cannot be checked here or by the drift guards.
_AT_CALL = re.compile(r'\bat\(\s*"([a-z0-9_]+)"')
_ALIAS_ENTRY = re.compile(r'"?([a-z0-9_]+)"?\s*:\s*"([a-z0-9_]+)"')


def _aliases() -> dict[str, str]:
    source = (FRONTEND_SRC / "lib" / "webapp" / "constants.ts").read_text(encoding="utf-8")
    block = source[source.index("LOCALE_KEY_ALIASES") :]
    return dict(_ALIAS_ENTRY.findall(block[: block.index("}")]))


def _resolve(key: str, aliases: dict[str, str]) -> str:
    seen: set[str] = set()
    while key in aliases and key not in seen:
        seen.add(key)
        key = aliases[key]
    return key


def _requested_keys() -> dict[str, set[str]]:
    """Admin locale keys per source file, already ``admin_``-prefixed."""

    aliases = _aliases()
    requested: dict[str, set[str]] = {}
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".svelte", ".ts"} or "node_modules" in path.parts:
            continue
        keys = set(_AT_CALL.findall(path.read_text(encoding="utf-8")))
        if keys:
            relative = str(path.relative_to(FRONTEND_SRC)).replace("\\", "/")
            requested[relative] = {_resolve(f"admin_{key}", aliases) for key in keys}
    return requested


def test_every_admin_string_is_translated_in_both_base_locales() -> None:
    requested = _requested_keys()
    assert requested, "no admin translation calls were found to check"

    for language in ("ru", "en"):
        data = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
        missing = {
            source: sorted(key for key in keys if not str(data.get(key, "")).strip())
            for source, keys in requested.items()
        }
        missing = {source: keys for source, keys in missing.items() if keys}
        assert not missing, (
            f"admin strings without a {language} translation "
            f"(the panel would show the English fallback): {missing}"
        )

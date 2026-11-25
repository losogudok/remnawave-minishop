"""Manifest entries for Progressive Web App (PWA) settings."""

from __future__ import annotations

from bot.app.web.admin_settings_manifest_types import SettingField

PWA_SETTINGS_FIELDS: list[SettingField] = [
    SettingField(
        "PWA_ENABLED",
        "bool",
        "appearance",
        "Installable Web App (PWA)",
        "Serve the Web App manifest and service worker so the Mini App can be "
        "installed as a standalone app. Disable to keep it browser/Telegram-only.",
        optional=False,
    ),
]

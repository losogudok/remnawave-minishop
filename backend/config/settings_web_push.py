"""Web Push settings for the built-in ``web_push`` plugin."""

from __future__ import annotations

from pydantic import Field, SecretStr


class WebPushSettings:
    WEB_PUSH_ENABLED: bool = Field(
        default=False,
        description=(
            "Enable browser Web Push delivery through the built-in web_push plugin. "
            "Requires VAPID keys and a contact subject."
        ),
    )
    WEB_PUSH_VAPID_PUBLIC_KEY: str = Field(
        default="",
        description="Base64url-encoded uncompressed P-256 VAPID public key (65 bytes).",
    )
    WEB_PUSH_VAPID_PRIVATE_KEY: SecretStr | None = Field(
        default=None,
        description="Base64url-encoded raw VAPID private scalar (32 bytes). Never expose.",
    )
    WEB_PUSH_VAPID_SUBJECT: str = Field(
        default="",
        description="VAPID contact subject: mailto: address or https:// URL.",
    )
    WEB_PUSH_DELIVERY_TTL_SECONDS: int = Field(
        default=3600,
        ge=0,
        le=86400,
        description="Push service retention time for queued messages, in seconds.",
    )
    SETUP_INSTALL_REWARD_DAYS: int = Field(default=0, ge=0, le=3650)
    SETUP_PUSH_REWARD_DAYS: int = Field(default=0, ge=0, le=3650)

    @property
    def web_push_configured(self) -> bool:
        """True when Web Push is enabled with a matching valid VAPID key pair."""

        if not self.WEB_PUSH_ENABLED:
            return False
        private_field = self.WEB_PUSH_VAPID_PRIVATE_KEY
        if private_field is None:
            private_key = ""
        elif hasattr(private_field, "get_secret_value"):
            private_key = private_field.get_secret_value()
        else:
            private_key = str(private_field)
        from config.web_push_keys import vapid_keys_valid, vapid_subject_valid

        return bool(
            vapid_keys_valid(self.WEB_PUSH_VAPID_PUBLIC_KEY, private_key)
            and vapid_subject_valid(self.WEB_PUSH_VAPID_SUBJECT)
        )

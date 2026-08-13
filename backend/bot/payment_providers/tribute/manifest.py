from ..base import ProviderManifestField

_CONFIG_MANIFEST = (
    ProviderManifestField(
        "TRIBUTE_ENABLED",
        "bool",
        "Enabled",
        description=(
            "Enable Tribute payments and signed webhook processing. Configured Creator "
            "links remain available when Shop Orders are disabled or unsupported."
        ),
        subsection="Tribute",
        attr="ENABLED",
    ),
    ProviderManifestField(
        "TRIBUTE_API_KEY",
        "string",
        "API key",
        description=(
            "API key for Tribute Shop/Creator API requests and HMAC-SHA256 verification "
            "of the trbt-signature webhook header."
        ),
        subsection="Tribute",
        secret=True,
        attr="API_KEY",
    ),
    ProviderManifestField(
        "TRIBUTE_SHOP_ID",
        "int",
        "Tribute Shop ID",
        description=(
            "Positive numeric ID of the exact Tribute Shop used to create orders and "
            "validate every Shop webhook. Required when the Shop API is enabled."
        ),
        subsection="Tribute",
        min=1,
        attr="SHOP_ID",
    ),
    ProviderManifestField(
        "TRIBUTE_SHOP_ENABLED",
        "bool",
        "Use Tribute Shop API",
        description=(
            "Use dynamic Shop Orders with exact local quotes as the primary flow. "
            "Recurring periods are limited to 1/3/6/12 months; unsupported contexts "
            "may use configured Creator links. Requires the numeric Tribute Shop ID."
        ),
        subsection="Tribute",
        attr="SHOP_ENABLED",
    ),
)

_PRESENTATION_MANIFEST = tuple(
    ProviderManifestField(
        key=key,
        type=type_,
        label=label,
        description=description,
        placeholder=placeholder,
        subsection="Tribute",
        target="presentation",
        attr=attr,
    )
    for key, type_, label, description, placeholder, attr in (
        (
            "PAYMENT_TRIBUTE_WEBAPP_LABEL_RU",
            "string",
            "WebApp button text (RU)",
            "Custom Russian text shown in the Web App payment method button.",
            "",
            "WEBAPP_LABEL_RU",
        ),
        (
            "PAYMENT_TRIBUTE_WEBAPP_LABEL_EN",
            "string",
            "WebApp button text (EN)",
            "Custom English text shown in the Web App payment method button.",
            "",
            "WEBAPP_LABEL_EN",
        ),
        (
            "PAYMENT_TRIBUTE_WEBAPP_ICON",
            "icon",
            "WebApp button icon",
            "Lucide icon name rendered inside the Web App payment method button.",
            "Gem",
            "WEBAPP_ICON",
        ),
        (
            "PAYMENT_TRIBUTE_TELEGRAM_LABEL_RU",
            "string",
            "Telegram button text (RU)",
            "Custom Russian text shown in Telegram bot payment buttons.",
            "",
            "TELEGRAM_LABEL_RU",
        ),
        (
            "PAYMENT_TRIBUTE_TELEGRAM_LABEL_EN",
            "string",
            "Telegram button text (EN)",
            "Custom English text shown in Telegram bot payment buttons.",
            "",
            "TELEGRAM_LABEL_EN",
        ),
        (
            "PAYMENT_TRIBUTE_TELEGRAM_EMOJI",
            "string",
            "Telegram button emoji",
            "Emoji prepended to the Telegram bot payment button when customized.",
            "💎",
            "TELEGRAM_EMOJI",
        ),
    )
)

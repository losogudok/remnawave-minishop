"""Admin-editable GA4 analytics settings."""

from bot.app.web.admin_settings_manifest_types import SettingField

ANALYTICS_SETTINGS_FIELDS: list[SettingField] = [
    # ─── Analytics (optional GA4 onboarding funnel) ────────────────
    SettingField(
        "GA4_ENABLED",
        "bool",
        "analytics",
        "GA4 onboarding funnel",
        (
            "Collects sanitized onboarding steps and sends authoritative activation, "
            "purchase and first-connection events to Google Analytics 4. Requires a "
            "valid measurement ID and API secret; otherwise nothing is sent."
        ),
        subsection="analytics",
        i18n_label_key="admin_settings_field_ga4_enabled_label",
        i18n_description_key="admin_settings_field_ga4_enabled_description",
        i18n_subsection_key="admin_settings_group_analytics",
    ),
    SettingField(
        "GA4_MEASUREMENT_ID",
        "string",
        "analytics",
        "GA4 Measurement ID",
        "Web stream measurement ID in the form G-XXXXXXXXXX. Empty disables analytics.",
        placeholder="G-XXXXXXXXXX",
        allow_empty=True,
        subsection="analytics",
        i18n_label_key="admin_settings_field_ga4_measurement_id_label",
        i18n_description_key="admin_settings_field_ga4_measurement_id_description",
        i18n_subsection_key="admin_settings_group_analytics",
    ),
    SettingField(
        "GA4_API_SECRET",
        "string",
        "analytics",
        "GA4 Measurement Protocol API secret",
        (
            "Server-only Measurement Protocol secret. It is never sent to the browser, "
            "logs or API responses. Empty disables authoritative server events."
        ),
        secret=True,
        allow_empty=True,
        subsection="analytics",
        i18n_label_key="admin_settings_field_ga4_api_secret_label",
        i18n_description_key="admin_settings_field_ga4_api_secret_description",
        i18n_subsection_key="admin_settings_group_analytics",
    ),
    SettingField(
        "GA4_DEBUG_MODE",
        "bool",
        "analytics",
        "GA4 debug mode",
        "Sends debug_mode to the browser tag so events appear in GA4 DebugView.",
        subsection="analytics",
        i18n_label_key="admin_settings_field_ga4_debug_mode_label",
        i18n_description_key="admin_settings_field_ga4_debug_mode_description",
        i18n_subsection_key="admin_settings_group_analytics",
    ),
]

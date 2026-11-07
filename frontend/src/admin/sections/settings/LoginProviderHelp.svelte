<script lang="ts">
  import { AdminButton } from "$components/patterns/admin/index.js";
  import { Check, Copy, ExternalLink, FileText } from "$components/ui/icons.js";
  import {
    loginProviderCallbackUrl,
    loginProviderGuideUrl,
    loginProviderHelpHint,
    loginProviderHelpTitle,
    loginProviderOfficialUrl,
  } from "$lib/admin/loginProviderSetup.js";

  let {
    at,
    provider,
    appRepositoryUrl,
    callbackBase,
  }: {
    at: (key: string, params?: Record<string, unknown>, fallback?: string) => string;
    provider: string;
    appRepositoryUrl: string;
    callbackBase: string;
  } = $props();
  let copiedLoginProviderKey = $state("");

  function loginProviderCallback(provider: "discord" | "google" | "yandex"): string {
    const fallbackOrigin = typeof window === "undefined" ? "" : window.location.origin;
    return loginProviderCallbackUrl(provider, callbackBase, fallbackOrigin);
  }

  async function copyLoginProviderValue(key: string, value: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(value);
      copiedLoginProviderKey = key;
    } catch {
      copiedLoginProviderKey = "";
    }
  }
</script>

{#if provider === "discord" || provider === "google" || provider === "yandex" || provider === "passkey"}
  <div class="admin-login-provider-help">
    <div class="admin-login-provider-help-copy">
      <strong>{loginProviderHelpTitle(provider, at)}</strong>
      <p>{loginProviderHelpHint(provider, at)}</p>
      {#if provider === "google"}
        <div class="admin-login-provider-setup-values">
          <div class="admin-login-provider-setup-row">
            <span>
              {at("settings_login_google_js_origins_label", {}, "Authorized JavaScript origins")}
            </span>
            <p>
              {at(
                "settings_login_google_js_origins_not_required",
                {},
                "Leave empty — Minishop uses the server-side authorization code flow and does not load the Google JavaScript SDK."
              )}
            </p>
          </div>
          <div class="admin-login-provider-setup-row">
            <span>
              {at("settings_login_google_redirect_uris_label", {}, "Authorized redirect URIs")}
            </span>
            {@render renderLoginProviderValue("google-redirect", loginProviderCallback("google"))}
          </div>
        </div>
      {:else if provider === "yandex"}
        <div class="admin-login-provider-setup-values">
          <div class="admin-login-provider-setup-row">
            <span>{at("settings_login_yandex_platform_label", {}, "Platform")}</span>
            <p>{at("settings_login_yandex_platform_value", {}, "Web services")}</p>
          </div>
          <div class="admin-login-provider-setup-row">
            <span>
              {at("settings_login_yandex_redirect_uri_label", {}, "Redirect URI")}
            </span>
            {@render renderLoginProviderValue("yandex-redirect", loginProviderCallback("yandex"))}
          </div>
          <div class="admin-login-provider-setup-row">
            <span>{at("settings_login_yandex_permissions_label", {}, "Permissions")}</span>
            <code>login:email · login:info · login:avatar</code>
          </div>
        </div>
      {:else if provider === "discord"}
        <div class="admin-login-provider-setup-values">
          <div class="admin-login-provider-setup-row">
            <span>{at("settings_login_discord_redirect_uri_label", {}, "Redirect URL")}</span>
            {@render renderLoginProviderValue("discord-redirect", loginProviderCallback("discord"))}
          </div>
          <div class="admin-login-provider-setup-row">
            <span>{at("settings_login_discord_scopes_label", {}, "OAuth2 scopes")}</span>
            <code>identify · email</code>
          </div>
        </div>
      {/if}
      <p class="admin-login-provider-runtime-hint">
        {at(
          "settings_login_runtime_hint",
          {},
          "Saved provider settings apply immediately; a backend restart is not required."
        )}
      </p>
    </div>
    <div class="admin-login-provider-help-actions">
      <a
        class="admin-btn admin-btn-sm admin-btn-ghost"
        href={loginProviderGuideUrl(provider, appRepositoryUrl)}
        target="_blank"
        rel="noreferrer noopener"
      >
        <FileText size={13} />
        <span>{at("settings_login_open_guide", {}, "Setup guide")}</span>
      </a>
      <a
        class="admin-btn admin-btn-sm admin-btn-ghost"
        href={loginProviderOfficialUrl(provider)}
        target="_blank"
        rel="noreferrer noopener"
      >
        <ExternalLink size={13} />
        <span>{at("settings_login_open_official", {}, "Official docs")}</span>
      </a>
    </div>
  </div>
{/if}

{#snippet renderLoginProviderValue(key: string, value: string)}
  <div class="admin-login-provider-value">
    <code>{value}</code>
    <AdminButton
      size="sm"
      variant="ghost"
      aria-label={at("copy", {}, "Copy")}
      onclick={() => copyLoginProviderValue(key, value)}
    >
      {#if copiedLoginProviderKey === key}
        <Check size={13} />{at("copied", {}, "Copied")}
      {:else}
        <Copy size={13} />{at("copy", {}, "Copy")}
      {/if}
    </AdminButton>
  </div>
{/snippet}

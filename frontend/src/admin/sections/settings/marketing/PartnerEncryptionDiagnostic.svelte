<script lang="ts">
  import { AdminBadge } from "$components/patterns/admin/index.js";
  import { Key } from "$components/ui/icons.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

  let {
    at,
    encryptionAvailable,
  }: {
    at: TranslateFn;
    encryptionAvailable: boolean;
  } = $props();

  const GENERATION_COMMAND = "openssl rand -base64 32 | tr '+/' '-_'";
  const ENV_EXAMPLE = "PARTNER_REQUISITES_ENCRYPTION_KEY=PASTE_GENERATED_VALUE_HERE";
</script>

<div class={encryptionAvailable ? "success" : "danger"}>
  <Key size={18} />
  <span>
    <strong>
      {encryptionAvailable
        ? at("partner_settings_encryption_ready", {}, "Requisites encryption is ready")
        : at("partner_settings_encryption_missing", {}, "Requisites encryption key is not set")}
    </strong>
    <small>
      {encryptionAvailable
        ? at(
            "partner_settings_encryption_ready_hint",
            {},
            "New withdrawal requisites can be encrypted and revealed through audited admin actions."
          )
        : at(
            "partner_settings_encryption_missing_hint",
            {},
            "Card numbers, phone numbers, and wallet addresses are encrypted at rest with this key. Until it is set, a withdrawal request fails instead of storing requisites in plain text."
          )}
    </small>
    <code>PARTNER_REQUISITES_ENCRYPTION_KEY</code>
    {#if !encryptionAvailable}
      <div class="partner-encryption-setup">
        <small>
          {at(
            "partner_settings_encryption_generate_hint",
            {},
            "Generate a 32-byte key with Python 3:"
          )}
        </small>
        <code class="partner-encryption-command" data-partner-encryption-command
          >{GENERATION_COMMAND}</code
        >
        <small>
          {at(
            "partner_settings_encryption_activate_hint",
            {},
            "Put the generated value in .env, restart backend and worker, and never commit the secret to Git."
          )}
        </small>
        <code class="partner-encryption-command">{ENV_EXAMPLE}</code>
      </div>
    {/if}
  </span>
  <AdminBadge variant={encryptionAvailable ? "success" : "danger"}>
    {encryptionAvailable
      ? at("partner_settings_ready", {}, "Ready")
      : at("partner_settings_action_required", {}, "Action required")}
  </AdminBadge>
</div>

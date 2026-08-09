<script lang="ts">
  import { Switch } from "$components/ui/primitives.js";
  import type { Snippet } from "svelte";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

  let {
    at,
    welcomeEnabled,
    paymentEnabled,
    onWelcomeChange,
    onPaymentChange,
  }: {
    at: TranslateFn;
    welcomeEnabled: boolean;
    paymentEnabled: boolean;
    onWelcomeChange: (checked: boolean) => void;
    onPaymentChange: (checked: boolean) => void;
  } = $props();

  function switchStateLabel(checked: boolean): string {
    return checked ? at("enabled", {}, "Enabled") : at("disabled", {}, "Disabled");
  }
</script>

{#snippet settingRow(label: string, envKey: string, control: Snippet)}
  <div class="admin-setting">
    <div class="admin-setting-meta">
      <strong>{label}</strong>
      <code>{envKey}</code>
    </div>
    <div class="admin-setting-control">{@render control()}</div>
  </div>
{/snippet}

{#snippet switchControl(checked: boolean, label: string, onChange: (next: boolean) => void)}
  <div class="admin-setting-switch">
    <Switch.Root {checked} aria-label={label} onCheckedChange={onChange} class="admin-switch-root">
      <Switch.Thumb class="admin-switch-thumb" />
    </Switch.Root>
    <span>{switchStateLabel(checked)}</span>
  </div>
{/snippet}

<section class="admin-settings-field-group">
  <header class="admin-settings-field-group-head">
    <strong>{at("partner_settings_client_bonuses_title", {}, "Client bonuses")}</strong>
    <small>
      {at(
        "partner_settings_client_bonuses_hint",
        {},
        "Uses the referral welcome days and each tariff's referee bonus matrix."
      )}
    </small>
  </header>
  <div class="admin-settings-field-group-body">
    {#snippet clientWelcomeBonusControl()}
      {@render switchControl(
        welcomeEnabled,
        at(
          "partner_settings_client_welcome_bonus",
          {},
          "Grant a welcome bonus after registration through a partner link"
        ),
        onWelcomeChange
      )}
    {/snippet}
    {@render settingRow(
      at(
        "partner_settings_client_welcome_bonus",
        {},
        "Grant a welcome bonus after registration through a partner link"
      ),
      "PARTNER_CLIENT_WELCOME_BONUS_ENABLED",
      clientWelcomeBonusControl
    )}

    {#snippet clientPaymentBonusControl()}
      {@render switchControl(
        paymentEnabled,
        at(
          "partner_settings_client_payment_bonus",
          {},
          "Grant referral bonus days to partner clients after payment"
        ),
        onPaymentChange
      )}
    {/snippet}
    {@render settingRow(
      at(
        "partner_settings_client_payment_bonus",
        {},
        "Grant referral bonus days to partner clients after payment"
      ),
      "PARTNER_CLIENT_PAYMENT_BONUS_ENABLED",
      clientPaymentBonusControl
    )}
  </div>
</section>

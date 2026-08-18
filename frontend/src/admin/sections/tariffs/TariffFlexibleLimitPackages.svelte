<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { Input } from "$components/ui/index.js";
  import { Switch } from "$components/ui/primitives.js";
  import { AdminSettingCard, AdminSettingsGroup } from "$components/patterns/admin/index.js";
  import type { TariffDraft } from "$lib/admin/stores/tariffsStore";
  import type { TranslateFn } from "./tariffEditorTabUtils.js";

  let {
    at,
    baseUnits,
    currencyCode,
    premium = false,
  }: {
    at: TranslateFn;
    baseUnits: number;
    currencyCode: string;
    premium?: boolean;
  } = $props();

  const tariffsStore = getTariffsStore();
  const state = $derived(tariffsStore);
  const draft: TariffDraft = $derived(state.tariffDraft);
  const prefix = $derived(premium ? "premium_flexible_traffic" : "flexible_traffic");
  const enabledField = $derived(
    premium ? "checkout_premium_traffic_enabled" : "checkout_traffic_enabled"
  );
  const stepField = $derived(`${prefix}_step_gb`);
  const maximumField = $derived(`${prefix}_max_total_gb`);
  const priceField = $derived(`${prefix}_price_per_step`);
  const starsField = $derived(`${prefix}_stars_price_per_step`);
  const stepGb = $derived(Number(draft[stepField] || 0));
  const maximum = $derived(Number(draft[maximumField] || 0));
  const pricePerStep = $derived(Number(draft[priceField] || 0));
  const starsPerStep = $derived(Number(draft[starsField] || 0));
  const stepCountRaw = $derived(stepGb > 0 ? (maximum - baseUnits) / stepGb : 0);
  const stepCount = $derived(Math.round(stepCountRaw));
  const valid = $derived(
    stepGb > 0 &&
      maximum > baseUnits &&
      pricePerStep >= 0 &&
      stepCount > 0 &&
      Math.abs(stepCountRaw - stepCount) < 1e-9
  );
  const preview = $derived(
    valid
      ? Array.from({ length: stepCount }, (_, index) => ({
          total: baseUnits + stepGb * (index + 1),
          price: pricePerStep * (index + 1),
          stars: starsPerStep * (index + 1),
        }))
      : []
  );

  function update(field: string, event: Event): void {
    tariffsStore.updateDraftField(field, (event.currentTarget as HTMLInputElement).value);
  }
</script>

<AdminSettingsGroup
  title={premium
    ? at("tariff_premium_flexible_limit_title", {}, "Premium flexible limit")
    : at("tariff_flexible_limit_title", {}, "Flexible traffic limit")}
  description={at(
    "tariff_flexible_limit_hint",
    {},
    "A resettable quota with an independent slider step and monthly price per step."
  )}
>
  <AdminSettingCard
    title={at("tariff_flexible_limit_step", {}, "Slider step, GB")}
    description={at("tariff_flexible_limit_step_hint", {}, "GB added by one slider mark")}
  >
    <Input
      class="input"
      type="number"
      min="0.1"
      step="0.1"
      value={String(draft[stepField] ?? "")}
      oninput={(event) => update(stepField, event)}
    />
  </AdminSettingCard>
  <AdminSettingCard
    title={at("tariff_checkout_addon_maximum", {}, "Maximum total value")}
    description={at(
      "tariff_flexible_limit_maximum_hint",
      {},
      "Base limit plus a whole number of steps"
    )}
  >
    <Input
      class="input"
      type="number"
      min={String(Math.max(0, baseUnits))}
      step="0.1"
      value={String(draft[maximumField] ?? "")}
      oninput={(event) => update(maximumField, event)}
    />
  </AdminSettingCard>
  <AdminSettingCard
    title={at(
      "tariff_flexible_limit_price_per_step",
      { currency: currencyCode },
      `Monthly price per step, ${currencyCode}`
    )}
  >
    <Input
      class="input"
      type="number"
      min="0"
      step="0.01"
      value={String(draft[priceField] ?? "")}
      oninput={(event) => update(priceField, event)}
    />
  </AdminSettingCard>
  <AdminSettingCard
    title={at("tariff_flexible_limit_stars_per_step", {}, "Monthly price per step, ⭐ Stars")}
  >
    <Input
      class="input"
      type="number"
      min="0"
      step="1"
      value={String(draft[starsField] ?? "")}
      oninput={(event) => update(starsField, event)}
    />
  </AdminSettingCard>

  <AdminSettingCard
    title={at("tariff_checkout_addon_enabled", {}, "Show checkout slider")}
    description={at(
      "tariff_checkout_addon_base",
      { value: `${baseUnits} GB` },
      `Included: ${baseUnits} GB`
    )}
  >
    <div class="admin-setting-switch">
      <Switch.Root
        class="admin-switch-root"
        aria-label={at("tariff_checkout_addon_enabled", {}, "Show checkout slider")}
        checked={Boolean(draft[enabledField])}
        disabled={!valid}
        onCheckedChange={(checked) => tariffsStore.updateDraftField(enabledField, checked)}
      >
        <Switch.Thumb class="admin-switch-thumb" />
      </Switch.Root>
      <span>
        {draft[enabledField] ? at("enabled", {}, "Enabled") : at("disabled", {}, "Disabled")}
      </span>
    </div>
  </AdminSettingCard>

  {#if valid}
    <div class="tariff-checkout-addon-preview">
      <span>{baseUnits} GB</span>
      {#each preview as option (option.total)}
        <span>
          {option.total} GB
          <small>
            +{option.price}
            {currencyCode}
            {#if option.stars > 0}
              · {option.stars} {at("tariff_stars_unit", {}, "⭐ Stars")}{/if}
          </small>
        </span>
      {/each}
    </div>
  {:else}
    <p class="admin-inline-error">
      {at(
        "tariff_flexible_limit_invalid",
        {},
        "The maximum must equal the base limit plus a whole number of steps."
      )}
    </p>
  {/if}
</AdminSettingsGroup>

<style>
  .tariff-checkout-addon-preview {
    display: flex;
    flex-wrap: wrap;
    gap: 0.45rem;
  }

  .tariff-checkout-addon-preview > span {
    display: inline-flex;
    align-items: baseline;
    gap: 0.35rem;
    padding: 0.35rem 0.55rem;
    border: 1px solid var(--admin-border);
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
  }

  .tariff-checkout-addon-preview small {
    color: var(--admin-muted);
    font-size: 0.68rem;
  }

  .admin-inline-error {
    margin: 0;
    color: var(--admin-danger, #ef4444);
    font-size: 0.75rem;
  }
</style>

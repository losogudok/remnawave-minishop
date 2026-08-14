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
  }: {
    at: TranslateFn;
    baseUnits: number;
    currencyCode: string;
  } = $props();

  const tariffsStore = getTariffsStore();
  const state = $derived(tariffsStore);
  const draft: TariffDraft = $derived(state.tariffDraft);
  const maximumExtra = $derived(Number(draft.checkout_devices_max_extra || 0));
  const pricePerDevice = $derived(Number(draft.checkout_devices_price_per_device || 0));
  const starsPerDevice = $derived(Number(draft.checkout_devices_stars_price_per_device || 0));
  const hasConfiguredPrice = $derived(
    String(draft.checkout_devices_price_per_device ?? "").trim() !== ""
  );
  const valid = $derived(
    baseUnits > 0 &&
      Number.isInteger(maximumExtra) &&
      maximumExtra > 0 &&
      hasConfiguredPrice &&
      Number.isFinite(pricePerDevice) &&
      pricePerDevice >= 0 &&
      Number.isFinite(starsPerDevice) &&
      starsPerDevice >= 0
  );
  const preview = $derived(
    valid
      ? Array.from({ length: maximumExtra }, (_, index) => {
          const extra = index + 1;
          return {
            extra,
            total: baseUnits + extra,
            price: pricePerDevice * extra,
            stars: starsPerDevice * extra,
          };
        })
      : []
  );

  function update(field: string, event: Event): void {
    tariffsStore.updateDraftField(field, (event.currentTarget as HTMLInputElement).value);
  }
</script>

<AdminSettingsGroup
  title={at("tariff_checkout_addon_title", {}, "During subscription checkout")}
  description={at(
    "tariff_device_checkout_subtitle",
    {},
    "A separate linear price for devices purchased together with the subscription. Post-purchase packages are configured below."
  )}
>
  <AdminSettingCard
    title={at("tariff_checkout_addon_enabled", {}, "Show checkout slider")}
    description={at(
      "tariff_checkout_addon_base",
      { value: `${baseUnits}` },
      `Included: ${baseUnits}`
    )}
  >
    <div class="admin-setting-switch">
      <Switch.Root
        class="admin-switch-root"
        aria-label={at("tariff_checkout_addon_enabled", {}, "Show checkout slider")}
        checked={Boolean(draft.checkout_devices_enabled)}
        disabled={!valid}
        onCheckedChange={(checked) =>
          tariffsStore.updateDraftField("checkout_devices_enabled", checked)}
      >
        <Switch.Thumb class="admin-switch-thumb" />
      </Switch.Root>
      <span>
        {draft.checkout_devices_enabled
          ? at("enabled", {}, "Enabled")
          : at("disabled", {}, "Disabled")}
      </span>
    </div>
  </AdminSettingCard>

  <AdminSettingCard
    title={at("tariff_device_checkout_max_extra", {}, "Maximum additional devices")}
    description={at(
      "tariff_device_checkout_max_extra_hint",
      {},
      "The slider always advances by one device. This value limits how many devices can be added."
    )}
  >
    <Input
      class="input"
      type="number"
      min="1"
      step="1"
      placeholder="5"
      value={draft.checkout_devices_max_extra}
      aria-label={at("tariff_device_checkout_max_extra", {}, "Maximum additional devices")}
      oninput={(event) => update("checkout_devices_max_extra", event)}
    />
  </AdminSettingCard>

  <AdminSettingCard
    title={at(
      "tariff_device_checkout_price",
      { currency: currencyCode },
      `One device per month, ${currencyCode}`
    )}
    description={at(
      "tariff_device_checkout_price_hint",
      {},
      "The checkout price is this monthly amount multiplied by the selected device count and subscription term."
    )}
  >
    <Input
      class="input"
      type="number"
      min="0"
      step="0.01"
      placeholder="79"
      value={draft.checkout_devices_price_per_device}
      aria-label={at(
        "tariff_device_checkout_price",
        { currency: currencyCode },
        `One device per month, ${currencyCode}`
      )}
      oninput={(event) => update("checkout_devices_price_per_device", event)}
    />
  </AdminSettingCard>

  <AdminSettingCard
    title={at("tariff_device_checkout_stars_price", {}, "One device per month, ⭐ Stars")}
    description={at(
      "tariff_device_checkout_stars_price_hint",
      {},
      "Leave empty or set to zero when checkout devices should not be available with Stars."
    )}
  >
    <Input
      class="input"
      type="number"
      min="0"
      step="1"
      placeholder="40"
      value={draft.checkout_devices_stars_price_per_device}
      aria-label={at("tariff_device_checkout_stars_price", {}, "One device per month, ⭐ Stars")}
      oninput={(event) => update("checkout_devices_stars_price_per_device", event)}
    />
  </AdminSettingCard>

  {#if valid}
    <div class="tariff-checkout-addon-preview">
      <span>{baseUnits}</span>
      {#each preview as option (option.extra)}
        <span>
          {option.total}
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
        "tariff_device_checkout_invalid",
        {},
        "Set a finite device limit, a positive maximum, and a monthly price to enable the slider."
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

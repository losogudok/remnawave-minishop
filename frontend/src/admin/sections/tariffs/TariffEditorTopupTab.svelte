<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { Input, Sortable } from "$components/ui/index.js";
  import { Switch, Tabs } from "$components/ui/primitives.js";
  import {
    AdminButton,
    AdminSelect,
    AdminSettingCard,
    AdminSettingsGroup,
  } from "$components/patterns/admin/index.js";
  import { Plus, Trash2 } from "$components/ui/icons.js";
  import { trafficStrategyOptions as buildTrafficStrategyOptions } from "$lib/admin/tariffSettings";
  import type { TariffDraft, TariffsCatalog } from "$lib/admin/stores/tariffsStore";
  import {
    currencyPriceAriaLabel as formatCurrencyPriceAriaLabel,
    currencyPriceColumnLabel as formatCurrencyPriceColumnLabel,
    defaultCurrencyCode as getDefaultCurrencyCode,
    draftInputHandler,
    draftRowInputHandler,
    draftRowKey,
    moveDraftRowHandler,
    tributeSectionVisible as isTributeSectionVisible,
    type DraftRow,
    type ReorderHandler,
    type SelectOption,
    type TranslateFn,
  } from "./tariffEditorTabUtils.js";
  import TributeCatalogButton from "./TributeCatalogButton.svelte";
  import TributeCatalogIssues from "./TributeCatalogIssues.svelte";
  import TributeProductField from "./TributeProductField.svelte";
  import type { TributeDraftRow } from "$lib/admin/tributeCatalog";
  import TariffFlexibleLimitPackages from "./TariffFlexibleLimitPackages.svelte";

  let { at }: { at: TranslateFn } = $props();

  const tariffsStore = getTariffsStore();
  const tariffsState = $derived(tariffsStore);
  const tariffDraft: TariffDraft = $derived(tariffsState.tariffDraft);
  const tariffsCatalog: TariffsCatalog = $derived(tariffsState.tariffsCatalog);
  const defaultCurrencyCode = $derived(getDefaultCurrencyCode(tariffsCatalog));
  const currencyPriceColumnLabel = $derived(
    formatCurrencyPriceColumnLabel(at, defaultCurrencyCode)
  );
  const currencyPriceAriaLabel = $derived(formatCurrencyPriceAriaLabel(at, defaultCurrencyCode));
  const trafficStrategyOptions: SelectOption[] = $derived(buildTrafficStrategyOptions(at));
  // Tribute mapping is provider configuration, so it stays out of the editor
  // until the provider is switched on — unless this tariff still carries a
  // mapping, which has to stay reachable to be cleared.
  const showTribute = $derived(isTributeSectionVisible(tariffsState, tariffDraft));
  const moveTopupRow: ReorderHandler = moveDraftRowHandler(tariffsStore, "topupRows");

  const topupProductRows = $derived(tariffDraft.topupRows as TributeDraftRow[]);

  function addTopupRow(): void {
    tariffsStore.addDraftRow("topupRows", {
      gb: 10,
      price: "",
      stars: "",
      tribute_product_id: "",
      tribute_product_link: "",
    });
  }
</script>

<Tabs.Content value="topup" class="admin-tabs-content">
  {#if tariffDraft.billing_model === "period"}
    <AdminSettingsGroup
      title={at("tariff_traffic_base_group", {}, "Traffic limit and reset")}
      description={at(
        "tariff_traffic_base_group_hint",
        {},
        "The quota included in the tariff and the schedule that starts it again."
      )}
    >
      <AdminSettingCard
        title={at("tariff_label_traffic_limit", {}, "Monthly traffic limit, GB")}
        description={at(
          "tariff_hint_traffic_limit",
          {},
          "How many GB are included every month. 0 means unlimited traffic. Extra packages are configured separately below."
        )}
      >
        <Input
          class="input"
          type="number"
          min="0"
          step="0.1"
          placeholder="100"
          value={tariffDraft.monthly_gb}
          oninput={draftInputHandler(tariffsStore, "monthly_gb")}
        />
      </AdminSettingCard>

      <AdminSettingCard
        title={at("tariff_label_traffic_strategy", {}, "Traffic reset strategy")}
        description={at(
          "tariff_hint_traffic_strategy",
          {},
          "How often Remnawave resets the traffic counter for users on this tariff. The strategy is applied when the tariff is activated, renewed, or changed"
        )}
        alignStart
      >
        <div class="tariff-setting-control-stack">
          <AdminSelect
            value={String(tariffDraft.traffic_limit_strategy || "")}
            items={trafficStrategyOptions}
            placeholder={at(
              "tariff_traffic_strategy_inherit",
              {},
              "Use global USER_TRAFFIC_STRATEGY"
            )}
            ariaLabel={at("tariff_label_traffic_strategy", {}, "Traffic reset strategy")}
            onValueChange={(value) =>
              tariffsStore.updateDraftField("traffic_limit_strategy", value)}
          />
          {#if tariffDraft.traffic_limit_strategy === "MONTH_ROLLING"}
            <small class="admin-muted">
              {at(
                "tariff_hint_traffic_strategy_month_rolling",
                {},
                "The monthly cycle starts with the subscription. Remnawave owns the regular counter and reports the effective boundary as lastTrafficResetAt; selecting this strategy does not erase already recorded traffic."
              )}
            </small>
          {/if}
        </div>
      </AdminSettingCard>
    </AdminSettingsGroup>

    {#if Number(tariffDraft.monthly_gb || 0) > 0}
      <TariffFlexibleLimitPackages
        {at}
        baseUnits={Number(tariffDraft.monthly_gb || 0)}
        currencyCode={defaultCurrencyCode}
      />
    {/if}
    <section class="admin-editor-section">
      <header class="admin-editor-section-head">
        <div class="admin-editor-section-title">
          <strong>{at("tariff_topup_title", {}, "Traffic top-up over the monthly limit")}</strong>
          <small
            >{at(
              "tariff_topup_subtitle",
              {},
              "When a user runs out of the monthly limit, they can buy an extra package without changing the subscription term"
            )}</small
          >
        </div>
        <div class="admin-editor-section-actions">
          {#if showTribute}
            <TributeCatalogButton {at} />
          {/if}
          <AdminButton size="sm" onclick={addTopupRow}
            ><Plus size={12} /> {at("tariff_btn_package", {}, "Package")}</AdminButton
          >
        </div>
      </header>
      <AdminSettingCard
        title={at("tariff_topup_always_label", {}, "Top-up always available")}
        description={at(
          "tariff_topup_always_hint",
          {},
          "By default, regular traffic top-up appears to the user (in the mini app and bot menu) after at least 80% of the limit is used. Enable this to show the offer regardless of usage percentage."
        )}
      >
        <div class="admin-setting-switch">
          <Switch.Root
            aria-label={at("tariff_topup_always_label", {}, "Top-up always available")}
            checked={Boolean(tariffDraft.topup_always_available)}
            onCheckedChange={(value) =>
              tariffsStore.updateDraftField("topup_always_available", value)}
            class="admin-switch-root"
          >
            <Switch.Thumb class="admin-switch-thumb" />
          </Switch.Root>
          <span>
            {tariffDraft.topup_always_available
              ? at("enabled", {}, "Enabled")
              : at("disabled", {}, "Disabled")}
          </span>
        </div>
      </AdminSettingCard>
      {#if showTribute}
        <p class="admin-muted">
          {at(
            "tariff_tribute_products_hint",
            {},
            "Optional. Map each fixed traffic package to a Tribute Digital Product. Configure its price in Tribute to match this package; the local price is not sent to Tribute"
          )}
        </p>
        <TributeCatalogIssues {at} rows={topupProductRows} mode="product" />
      {/if}
      {#if tariffDraft.topupRows.length}
        <div class="admin-row-editor">
          <div
            class="admin-row-editor-line {showTribute
              ? 'admin-row-editor-tribute-product'
              : 'admin-row-editor-package'} admin-row-editor-header"
          >
            <span></span>
            <span>{at("tariff_col_volume_gb", {}, "Volume, GB")}</span>
            <span>{currencyPriceColumnLabel}</span>
            <span>{at("tariff_col_price_stars_full", {}, "⭐ Stars")}</span>
            {#if showTribute}
              <span>{at("tariff_col_tribute_product_id", {}, "Tribute product ID")}</span>
              <span>{at("tariff_col_tribute_product_link", {}, "Tribute product link")}</span>
            {/if}
            <span></span>
          </div>
          <Sortable
            items={tariffDraft.topupRows}
            class={`admin-row-editor-line ${showTribute ? "admin-row-editor-tribute-product" : "admin-row-editor-package"}`}
            getKey={draftRowKey}
            handleLabel={at("tariff_package_reorder", {}, "Drag to reorder")}
            onReorder={moveTopupRow}
          >
            {#snippet children(row: DraftRow, index: number)}
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_volume_gb", {}, "Volume, GB")}</span
              >
              <Input
                class="input"
                type="number"
                min="0.1"
                step="0.1"
                placeholder="20"
                value={row.gb}
                oninput={draftRowInputHandler(tariffsStore, "topupRows", index, "gb")}
                aria-label={at("tariff_col_volume_gb", {}, "Volume, GB")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{currencyPriceColumnLabel}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="0.01"
                placeholder="149"
                value={row.price}
                oninput={draftRowInputHandler(tariffsStore, "topupRows", index, "price")}
                aria-label={currencyPriceAriaLabel}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_price_stars_full", {}, "⭐ Stars")}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="1"
                placeholder="75"
                value={row.stars}
                oninput={draftRowInputHandler(tariffsStore, "topupRows", index, "stars")}
                aria-label={at("tariff_label_price_stars", {}, "⭐ Stars")}
              />
              {#if showTribute}
                <span class="admin-row-editor-mobile-label" aria-hidden="true"
                  >{at("tariff_col_tribute_product_id", {}, "Tribute product ID")}</span
                >
                <TributeProductField {at} field="topupRows" {index} {row} />
                <span class="admin-row-editor-mobile-label" aria-hidden="true"
                  >{at("tariff_col_tribute_product_link", {}, "Tribute product link")}</span
                >
                <Input
                  class="input"
                  type="url"
                  placeholder={at(
                    "tariff_placeholder_tribute_product_link",
                    {},
                    "https://t.me/tribute/app?startapp=..."
                  )}
                  value={row.tribute_product_link}
                  oninput={draftRowInputHandler(
                    tariffsStore,
                    "topupRows",
                    index,
                    "tribute_product_link"
                  )}
                  aria-label={at("tariff_label_tribute_product_link", {}, "Tribute product link")}
                />
              {/if}
              <AdminButton
                size="sm"
                variant="danger"
                onclick={() => tariffsStore.removeDraftRow("topupRows", index)}
                aria-label={at("btn_delete", {}, "Delete")}><Trash2 size={13} /></AdminButton
              >
            {/snippet}
          </Sortable>
        </div>
      {/if}
    </section>
  {:else}
    <p class="admin-muted">
      {at(
        "tariff_topup_traffic_hint",
        {},
        "For the traffic model, separate top-ups are not needed: packages configured on the Prices tab are the top-ups users can buy again as they run out."
      )}
    </p>
  {/if}
</Tabs.Content>

<style>
  .tariff-setting-control-stack {
    display: grid;
    gap: 7px;
    width: 100%;
    min-width: 0;
  }
</style>

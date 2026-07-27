<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { Input, Sortable } from "$components/ui/index.js";
  import { Label, Switch, Tabs } from "$components/ui/primitives.js";
  import { AdminButton } from "$components/patterns/admin/index.js";
  import { Plus, Trash2 } from "$components/ui/icons.js";
  import type { TariffDraft, TariffsCatalog } from "$lib/admin/stores/tariffsStore";
  import {
    currencyPriceAriaLabel as formatCurrencyPriceAriaLabel,
    currencyPriceColumnLabel as formatCurrencyPriceColumnLabel,
    defaultCurrencyCode as getDefaultCurrencyCode,
    draftRowInputHandler,
    draftRowKey,
    moveDraftRowHandler,
    tributeEnabled as isTributeEnabled,
    type DraftRow,
    type ReorderHandler,
    type TranslateFn,
  } from "./tariffEditorTabUtils.js";
  import TributeCatalogButton from "./TributeCatalogButton.svelte";
  import TributeCatalogIssues from "./TributeCatalogIssues.svelte";
  import TributeProductField from "./TributeProductField.svelte";
  import type { TributeDraftRow } from "$lib/admin/tributeCatalog";

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
  // Tribute mapping is provider configuration, so it stays out of the
  // editor until the provider itself is switched on.
  const tributeEnabled = $derived(isTributeEnabled(tariffsState));
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
          {#if tributeEnabled}
            <TributeCatalogButton {at} />
          {/if}
          <AdminButton size="sm" onclick={addTopupRow}
            ><Plus size={12} /> {at("tariff_btn_package", {}, "Package")}</AdminButton
          >
        </div>
      </header>
      <div class="admin-action-row admin-action-row-bordered">
        <Switch.Root
          aria-labelledby="tariff-topup-always-toggle-label"
          checked={Boolean(tariffDraft.topup_always_available)}
          onCheckedChange={(value) =>
            tariffsStore.updateDraftField("topup_always_available", value)}
          class="admin-switch-root"
        >
          <Switch.Thumb class="admin-switch-thumb" />
        </Switch.Root>
        <Label.Root id="tariff-topup-always-toggle-label" class="admin-action-label">
          <strong>{at("tariff_topup_always_label", {}, "Top-up always available")}</strong>
          <small
            >{at(
              "tariff_topup_always_hint",
              {},
              "By default, regular traffic top-up appears to the user (in the mini app and bot menu) after at least 80% of the limit is used. Enable this to show the offer regardless of usage percentage."
            )}</small
          >
        </Label.Root>
      </div>
      {#if tributeEnabled}
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
            class="admin-row-editor-line {tributeEnabled
              ? 'admin-row-editor-tribute-product'
              : 'admin-row-editor-package'} admin-row-editor-header"
          >
            <span></span>
            <span>{at("tariff_col_volume_gb", {}, "Volume, GB")}</span>
            <span>{currencyPriceColumnLabel}</span>
            <span>{at("tariff_col_price_stars_full", {}, "Price, ⭐ Stars")}</span>
            {#if tributeEnabled}
              <span>{at("tariff_col_tribute_product_id", {}, "Tribute product ID")}</span>
              <span>{at("tariff_col_tribute_product_link", {}, "Tribute product link")}</span>
            {/if}
            <span></span>
          </div>
          <Sortable
            items={tariffDraft.topupRows}
            class={`admin-row-editor-line ${tributeEnabled ? "admin-row-editor-tribute-product" : "admin-row-editor-package"}`}
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
                >{at("tariff_col_price_stars_full", {}, "Price, ⭐ Stars")}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="1"
                placeholder="75"
                value={row.stars}
                oninput={draftRowInputHandler(tariffsStore, "topupRows", index, "stars")}
                aria-label={at("tariff_label_price_stars", {}, "Price in Telegram Stars")}
              />
              {#if tributeEnabled}
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

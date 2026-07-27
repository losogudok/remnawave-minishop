<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { Input, Sortable } from "$components/ui/index.js";
  import { Label, Tabs } from "$components/ui/primitives.js";
  import { AdminButton } from "$components/patterns/admin/index.js";
  import { Plus, Trash2 } from "$components/ui/icons.js";
  import type { TariffDraft, TariffsCatalog } from "$lib/admin/stores/tariffsStore";
  import {
    currencyPriceAriaLabel as formatCurrencyPriceAriaLabel,
    currencyPriceColumnLabel as formatCurrencyPriceColumnLabel,
    defaultCurrencyCode as getDefaultCurrencyCode,
    draftInputHandler,
    draftRowInputHandler,
    draftRowKey,
    moveDraftRowHandler,
    type DraftRow,
    type ReorderHandler,
    type TranslateFn,
  } from "./tariffEditorTabUtils.js";

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
  const movePeriodRow: ReorderHandler = moveDraftRowHandler(tariffsStore, "periodRows");
  const moveTrafficRow: ReorderHandler = moveDraftRowHandler(tariffsStore, "trafficRows");

  function addPeriodRow(): void {
    tariffsStore.addDraftRow("periodRows", {
      months: 1,
      rub: "",
      stars: "",
      referral_inviter: "",
      referral_referee: "",
      tribute_period_id: "",
    });
  }

  function addTrafficRow(): void {
    tariffsStore.addDraftRow("trafficRows", {
      gb: 10,
      price: "",
      stars: "",
      tribute_product_id: "",
      tribute_product_link: "",
    });
  }
</script>

<Tabs.Content value="pricing" class="admin-tabs-content">
  {#if tariffDraft.billing_model === "period"}
    <section class="admin-editor-section">
      <header class="admin-editor-section-head">
        <div class="admin-editor-section-title">
          <strong>{at("tariff_pricing_period_title", {}, "Subscription periods and prices")}</strong
          >
          <small
            >{at(
              "tariff_pricing_period_subtitle",
              {},
              "Each row is a separate storefront option: how many months the user pays for and how much it costs. Drag rows by the handle to set the period order in the bot and the web app"
            )}</small
          >
        </div>
        <AdminButton size="sm" onclick={addPeriodRow}>
          <Plus size={13} />
          {at("tariff_btn_period", {}, "Period")}
        </AdminButton>
      </header>
      {#if !tariffDraft.periodRows.length}
        <p class="admin-muted">
          {at(
            "tariff_pricing_empty",
            {},
            "Add at least one period so the tariff appears in the storefront."
          )}
        </p>
      {:else}
        <div class="admin-row-editor">
          <div class="admin-row-editor-line admin-row-editor-period admin-row-editor-header">
            <span></span>
            <span>{at("tariff_col_period_months", {}, "Period, mo.")}</span>
            <span>{at("tariff_col_tribute_period_id", {}, "Tribute period ID")}</span>
            <span>{currencyPriceColumnLabel}</span>
            <span>{at("tariff_col_price_stars_full", {}, "Price, ⭐ Stars")}</span>
            <span>{at("tariff_col_ref_inviter", {}, "Inviter bonus")}</span>
            <span>{at("tariff_col_ref_referee", {}, "Friend bonus")}</span>
            <span></span>
          </div>
          <Sortable
            items={tariffDraft.periodRows}
            class="admin-row-editor-line admin-row-editor-period"
            getKey={draftRowKey}
            handleLabel={at("tariff_period_reorder", {}, "Drag to reorder")}
            onReorder={movePeriodRow}
          >
            {#snippet children(row: DraftRow, index: number)}
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_period_months", {}, "Period, mo.")}</span
              >
              <Input
                class="input"
                type="number"
                min="1"
                placeholder="1"
                value={row.months}
                oninput={draftRowInputHandler(tariffsStore, "periodRows", index, "months")}
                aria-label={at("tariff_col_period_months", {}, "Period, mo.")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_tribute_period_id", {}, "Tribute period ID")}</span
              >
              <Input
                class="input"
                type="number"
                min="1"
                step="1"
                placeholder={at("tariff_placeholder_tribute_period_id", {}, "e.g. 1001")}
                value={row.tribute_period_id}
                oninput={draftRowInputHandler(
                  tariffsStore,
                  "periodRows",
                  index,
                  "tribute_period_id"
                )}
                aria-label={at("tariff_label_tribute_period_id", {}, "Tribute period ID")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{currencyPriceColumnLabel}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="0.01"
                placeholder="299"
                value={row.rub}
                oninput={draftRowInputHandler(tariffsStore, "periodRows", index, "rub")}
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
                placeholder="150"
                value={row.stars}
                oninput={draftRowInputHandler(tariffsStore, "periodRows", index, "stars")}
                aria-label={at("tariff_label_price_stars", {}, "Price in Telegram Stars")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_ref_inviter", {}, "Inviter bonus")}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="1"
                placeholder="3"
                value={row.referral_inviter}
                oninput={draftRowInputHandler(
                  tariffsStore,
                  "periodRows",
                  index,
                  "referral_inviter"
                )}
                aria-label={at("tariff_label_ref_inviter", {}, "Inviter bonus, days")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_ref_referee", {}, "Friend bonus")}</span
              >
              <Input
                class="input"
                type="number"
                min="0"
                step="1"
                placeholder="1"
                value={row.referral_referee}
                oninput={draftRowInputHandler(
                  tariffsStore,
                  "periodRows",
                  index,
                  "referral_referee"
                )}
                aria-label={at("tariff_label_ref_referee", {}, "Friend bonus, days")}
              />
              <AdminButton
                size="sm"
                variant="danger"
                onclick={() => tariffsStore.removeDraftRow("periodRows", index)}
                aria-label={at("btn_delete", {}, "Delete")}
              >
                <Trash2 size={13} />
              </AdminButton>
            {/snippet}
          </Sortable>
        </div>
      {/if}
    </section>

    <section class="admin-editor-section">
      <header class="admin-editor-section-head">
        <div class="admin-editor-section-title">
          <strong>{at("tariff_tribute_title", {}, "Tribute subscription")}</strong>
          <small
            >{at(
              "tariff_tribute_subtitle",
              {},
              "Optional. Create the subscription in Tribute, then enter its public link and numeric subscription ID here. Tribute manages the invoice price and billing cycle; the local tariff prices above are not sent to Tribute"
            )}</small
          >
        </div>
      </header>
      <div class="admin-form-row admin-form-row-2">
        <Label.Root class="admin-field-label">
          <span>{at("tariff_label_tribute_link", {}, "Tribute subscription link")}</span>
          <small
            >{at(
              "tariff_hint_tribute_link",
              {},
              "Use the HTTPS link published by Tribute. Leave all Tribute fields empty to disable it for this tariff"
            )}</small
          >
          <Input
            class="input"
            type="url"
            placeholder={at(
              "tariff_placeholder_tribute_link",
              {},
              "https://t.me/tribute/app?startapp=..."
            )}
            value={tariffDraft.tributeLink}
            oninput={draftInputHandler(tariffsStore, "tributeLink")}
            aria-label={at("tariff_label_tribute_link", {}, "Tribute subscription link")}
          />
        </Label.Root>

        <Label.Root class="admin-field-label">
          <span>{at("tariff_label_tribute_subscription_id", {}, "Tribute subscription ID")}</span>
          <small
            >{at(
              "tariff_hint_tribute_subscription_id",
              {},
              "Numeric subscription_id from Tribute. For each period above, also enter the matching period_id"
            )}</small
          >
          <Input
            class="input"
            type="number"
            min="1"
            step="1"
            placeholder={at("tariff_placeholder_tribute_subscription_id", {}, "e.g. 101")}
            value={tariffDraft.tributeSubscriptionId}
            oninput={draftInputHandler(tariffsStore, "tributeSubscriptionId")}
            aria-label={at("tariff_label_tribute_subscription_id", {}, "Tribute subscription ID")}
          />
        </Label.Root>
      </div>
    </section>
  {:else}
    <section class="admin-editor-section">
      <header class="admin-editor-section-head">
        <div class="admin-editor-section-title">
          <strong>{at("tariff_pricing_traffic_title", {}, "Traffic packages")}</strong>
          <small
            >{at(
              "tariff_pricing_traffic_subtitle",
              {},
              'Base storefront for the traffic model. Each row is an "N gigabytes for N currency units" package. Drag rows by the handle to set the package order in the bot and the web app'
            )}</small
          >
        </div>
        <div class="admin-editor-section-actions">
          <AdminButton size="sm" onclick={addTrafficRow}
            ><Plus size={12} /> {at("tariff_btn_package", {}, "Package")}</AdminButton
          >
        </div>
      </header>
      <p class="admin-muted">
        {at(
          "tariff_tribute_products_hint",
          {},
          "Optional. Map each fixed traffic package to a Tribute Digital Product. Configure its price in Tribute to match this package; the local price is not sent to Tribute"
        )}
      </p>
      {#if tariffDraft.trafficRows.length}
        <div class="admin-row-editor">
          <div
            class="admin-row-editor-line admin-row-editor-tribute-product admin-row-editor-header"
          >
            <span></span>
            <span>{at("tariff_col_volume_gb", {}, "Volume, GB")}</span>
            <span>{currencyPriceColumnLabel}</span>
            <span>{at("tariff_col_price_stars_full", {}, "Price, ⭐ Stars")}</span>
            <span>{at("tariff_col_tribute_product_id", {}, "Tribute product ID")}</span>
            <span>{at("tariff_col_tribute_product_link", {}, "Tribute product link")}</span>
            <span></span>
          </div>
          <Sortable
            items={tariffDraft.trafficRows}
            class="admin-row-editor-line admin-row-editor-tribute-product"
            getKey={draftRowKey}
            handleLabel={at("tariff_package_reorder", {}, "Drag to reorder")}
            onReorder={moveTrafficRow}
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
                placeholder="50"
                value={row.gb}
                oninput={draftRowInputHandler(tariffsStore, "trafficRows", index, "gb")}
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
                placeholder="299"
                value={row.price}
                oninput={draftRowInputHandler(tariffsStore, "trafficRows", index, "price")}
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
                placeholder="150"
                value={row.stars}
                oninput={draftRowInputHandler(tariffsStore, "trafficRows", index, "stars")}
                aria-label={at("tariff_label_price_stars", {}, "Price in Telegram Stars")}
              />
              <span class="admin-row-editor-mobile-label" aria-hidden="true"
                >{at("tariff_col_tribute_product_id", {}, "Tribute product ID")}</span
              >
              <Input
                class="input"
                type="number"
                min="1"
                step="1"
                placeholder={at("tariff_placeholder_tribute_product_id", {}, "e.g. 501")}
                value={row.tribute_product_id}
                oninput={draftRowInputHandler(
                  tariffsStore,
                  "trafficRows",
                  index,
                  "tribute_product_id"
                )}
                aria-label={at("tariff_label_tribute_product_id", {}, "Tribute product ID")}
              />
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
                  "trafficRows",
                  index,
                  "tribute_product_link"
                )}
                aria-label={at("tariff_label_tribute_product_link", {}, "Tribute product link")}
              />
              <AdminButton
                size="sm"
                variant="danger"
                onclick={() => tariffsStore.removeDraftRow("trafficRows", index)}
                aria-label={at("btn_delete", {}, "Delete")}><Trash2 size={13} /></AdminButton
              >
            {/snippet}
          </Sortable>
        </div>
      {/if}
    </section>
  {/if}
</Tabs.Content>

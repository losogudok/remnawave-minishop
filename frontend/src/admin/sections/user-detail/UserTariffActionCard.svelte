<script lang="ts">
  import { getTariffsStore, getUsersStore } from "$lib/admin/context";
  import {
    AdminBadge,
    AdminButton,
    AdminSectionHeader,
    AdminSelect,
  } from "$components/patterns/admin/index.js";
  import { Label } from "$components/ui/primitives.js";
  import { RefreshCw } from "$components/ui/icons.js";
  import type { SelectOption, TranslateFn } from "./userDetailTypes";

  let {
    at,
    userActionBusy = false,
    periodTariffItems = [],
    tariffActionDirty = false,
    tariffHwidLimitChangeAvailable = false,
    currentSubscriptionTariffLabel = "",
    userTariffActionKey = "",
    selectTariffAction,
  }: {
    at: TranslateFn;
    userActionBusy?: boolean;
    periodTariffItems?: SelectOption[];
    tariffActionDirty?: boolean;
    tariffHwidLimitChangeAvailable?: boolean;
    currentSubscriptionTariffLabel?: string;
    userTariffActionKey?: string;
    selectTariffAction: (value: string) => void;
  } = $props();

  const usersStore = getUsersStore();
  const tariffsStore = getTariffsStore();
  const selectedTariff = $derived(
    (tariffsStore.tariffsCatalog?.tariffs || []).find(
      (tariff) => String(tariff?.key || "") === userTariffActionKey
    ) || null
  );
  const selectedTariffBaseSquads = $derived(
    new Set((selectedTariff?.squad_uuids || []).map((uuid) => String(uuid))).size
  );
  const selectedTariffPremiumSquads = $derived(
    new Set((selectedTariff?.premium_squad_uuids || []).map((uuid) => String(uuid))).size
  );

  function trafficPreviewLabel(): string {
    const monthlyGb = Number(selectedTariff?.monthly_gb || 0);
    if (monthlyGb <= 0) return at("tariff_traffic_unlimited", {}, "Unlimited");
    return at("user_tariff_preview_traffic_gb", { count: monthlyGb }, `${monthlyGb} GB / month`);
  }

  function hwidPreviewLabel(): string {
    const raw = selectedTariff?.hwid_device_limit;
    if (raw === null || raw === undefined) {
      return at("user_hwid_limit_default", {}, "Tariff / default");
    }
    const limit = Number(raw);
    if (limit === 0) return at("user_hwid_limit_unlimited", {}, "Unlimited");
    return at("user_hwid_limit_count", { count: limit }, `${limit}`);
  }

  function saveTariff() {
    if (tariffHwidLimitChangeAvailable) {
      usersStore.updateState({ userTariffHwidConfirmOpen: true });
      return;
    }
    usersStore.changeUserTariff();
  }
</script>

<section
  class="admin-user-action-sheet admin-user-action-sheet--tariff"
  class:is-dirty={tariffActionDirty}
>
  <AdminSectionHeader
    title={at("user_tariff_card_title", {}, "Tariff")}
    description={at(
      "user_tariff_card_hint",
      {},
      "Change the user's tariff and sync panel squads immediately."
    )}
  />
  <div class="admin-user-action-sheet-body admin-user-tariff-stack">
    <Label.Root class="admin-field-label admin-extend-field">
      <span>{at("user_tariff_select_label", {}, "Tariff")}</span>
      <AdminSelect
        class="admin-user-tariff-select"
        value={usersStore.userTariffActionKey}
        items={periodTariffItems}
        placeholder={at("user_tariff_select_placeholder", {}, "Select tariff")}
        ariaLabel={at("user_tariff_select_label", {}, "Tariff")}
        disabled={userActionBusy}
        onValueChange={selectTariffAction}
      />
    </Label.Root>
  </div>
  <div class="admin-user-action-sheet-footer admin-override-card-footer">
    <div class="admin-override-card-toolbar">
      <span class="admin-meta-truncate">
        {at(
          "user_tariff_current",
          { tariff: currentSubscriptionTariffLabel },
          `Current: ${currentSubscriptionTariffLabel}`
        )}
      </span>
      <div class="admin-action-save-controls">
        {#if tariffActionDirty}
          <AdminBadge variant="warning">{at("settings_badge_dirty", {}, "Changed")}</AdminBadge>
        {/if}
        <AdminButton
          variant="primary"
          onclick={saveTariff}
          disabled={userActionBusy || !userTariffActionKey || !tariffActionDirty}
        >
          <RefreshCw size={14} />
          {at("user_tariff_save", {}, "Save tariff")}
        </AdminButton>
      </div>
    </div>
    {#if tariffActionDirty}
      <div class="admin-override-status-lines">
        {#if selectedTariff}
          <strong>{at("user_tariff_preview_title", {}, "Result after tariff change")}</strong>
          <div class="admin-provider-summary">
            <AdminBadge variant="muted">
              {at("user_tariff_preview_traffic", {}, "Main traffic")}: {trafficPreviewLabel()}
            </AdminBadge>
            <AdminBadge variant="muted">
              {at("user_tariff_preview_squads", {}, "Squads")}:
              {selectedTariffBaseSquads} + {selectedTariffPremiumSquads}
            </AdminBadge>
            <AdminBadge variant="muted">
              {at("user_tariff_preview_hwid", {}, "HWID limit")}: {hwidPreviewLabel()}
            </AdminBadge>
          </div>
          <span class="admin-muted">
            {at(
              "user_tariff_preview_hint",
              {},
              "Tariff squads and limits are synced to Remnawave; manual squad additions remain."
            )}
          </span>
        {/if}
        <span class="admin-unsaved-hint">
          {at("user_action_unsaved_hint", {}, "Unsaved changes in this card")}
        </span>
        {#if tariffHwidLimitChangeAvailable}
          <span class="admin-muted">
            {at(
              "user_tariff_hwid_confirm_hint",
              {},
              "The manual HWID limit will be preserved; you can apply the tariff limit before saving."
            )}
          </span>
        {/if}
      </div>
    {/if}
  </div>
</section>

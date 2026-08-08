<script lang="ts">
  import {
    ArrowDown,
    ArrowUp,
    Bitcoin,
    Copy,
    CreditCard,
    Plus,
    Trash2,
    TriangleAlert,
    WalletCards,
  } from "$components/ui/icons.js";
  import { Input, Switch } from "$components/ui/index.js";
  import {
    AdminBadge,
    AdminButton,
    AdminEmptyState,
    AdminField,
    AdminSelect,
  } from "$components/patterns/admin/index.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type MethodType = "bank_card" | "sbp" | "crypto";
  type CryptoNetwork = { id: string; label: string };
  type WithdrawalMethod = {
    id: string;
    type: MethodType;
    enabled: boolean;
    label: string;
    currency: string;
    scale: number;
    minimum: number;
    maximum: number | null;
    networks: CryptoNetwork[];
  };

  let {
    at,
    methods,
    methodEditor = $bindable(),
    duplicateMethodIds,
    cryptoInvalid,
    addMethod,
    moveMethod,
    duplicateMethod,
    deleteMethod,
    updateMethod,
    addNetwork,
    updateNetwork,
    removeNetwork,
    networkConfigurationInvalid,
    methodTitle,
    switchStateLabel,
  }: {
    at: TranslateFn;
    methods: WithdrawalMethod[];
    methodEditor: number | null;
    duplicateMethodIds: string[];
    cryptoInvalid: boolean;
    addMethod: (type: MethodType) => void;
    moveMethod: (index: number, offset: -1 | 1) => void;
    duplicateMethod: (index: number) => void;
    deleteMethod: (index: number) => void;
    updateMethod: (index: number, updates: Partial<WithdrawalMethod>) => void;
    addNetwork: (index: number) => void;
    updateNetwork: (
      methodIndex: number,
      networkIndex: number,
      updates: Partial<CryptoNetwork>
    ) => void;
    removeNetwork: (methodIndex: number, networkIndex: number) => void;
    networkConfigurationInvalid: (method: WithdrawalMethod) => boolean;
    methodTitle: (method: WithdrawalMethod) => string;
    switchStateLabel: (checked: boolean) => string;
  } = $props();
</script>

{#snippet switchControl(checked: boolean, label: string, onChange: (next: boolean) => void)}
  <div class="admin-setting-switch">
    <Switch.Root {checked} aria-label={label} onCheckedChange={onChange} class="admin-switch-root">
      <Switch.Thumb class="admin-switch-thumb" />
    </Switch.Root>
    <span>{switchStateLabel(checked)}</span>
  </div>
{/snippet}

<section class="admin-settings-field-group partner-methods-section">
  <header class="admin-settings-field-group-head">
    <div class="admin-settings-field-group-head-copy">
      <strong>{at("partner_settings_methods_title", {}, "Withdrawal methods")}</strong>
      <small>
        {at(
          "partner_settings_methods_hint",
          {},
          "Methods are ordered as shown to partners. Existing requests keep their snapshots."
        )}
      </small>
      <code>PARTNER_WITHDRAWAL_METHODS_JSON</code>
    </div>
    <div class="partner-method-add">
      <AdminButton size="sm" onclick={() => addMethod("bank_card")}>
        <Plus size={14} />{at("partner_settings_add_card", {}, "Card")}
      </AdminButton>
      <AdminButton size="sm" onclick={() => addMethod("sbp")}>
        <Plus size={14} />{at("partner_settings_add_sbp", {}, "SBP")}
      </AdminButton>
      <AdminButton size="sm" onclick={() => addMethod("crypto")}>
        <Plus size={14} />{at("partner_settings_add_crypto", {}, "Crypto")}
      </AdminButton>
    </div>
  </header>

  <div class="partner-method-list">
    {#if !methods.length}
      <AdminEmptyState class="partner-method-empty">
        <WalletCards size={23} />
        <strong>
          {at("partner_settings_methods_empty", {}, "No withdrawal methods configured")}
        </strong>
        <p>
          {at(
            "partner_settings_methods_empty_hint",
            {},
            "Balances continue to accrue, but partners cannot request a payout."
          )}
        </p>
      </AdminEmptyState>
    {/if}
    {#each methods as method, index (method.id)}
      <article
        class="partner-method-card"
        class:invalid={duplicateMethodIds.includes(method.id) ||
          networkConfigurationInvalid(method)}
      >
        <button
          class="partner-method-main"
          type="button"
          aria-expanded={methodEditor === index}
          onclick={() => (methodEditor = methodEditor === index ? null : index)}
        >
          <span class="partner-method-icon">
            {#if method.type === "crypto"}<Bitcoin size={19} />{:else}<CreditCard size={19} />{/if}
          </span>
          <span>
            <strong>{methodTitle(method)}</strong>
            <small
              >{method.id} · {method.currency} · {at(
                "partner_settings_method_min",
                { amount: method.minimum },
                "min {amount}"
              )}{#if method.type === "crypto"}&nbsp;· {method.networks
                  .map((network) => network.label)
                  .filter(Boolean)
                  .join(", ") || at("partner_settings_no_networks", {}, "no networks")}{/if}</small
            >
          </span>
          <AdminBadge variant={method.enabled ? "success" : "muted"}>
            {switchStateLabel(method.enabled)}
          </AdminBadge>
        </button>
        <div class="partner-method-actions">
          <AdminButton
            size="sm"
            variant="ghost"
            onclick={() => moveMethod(index, -1)}
            disabled={index === 0}
            aria-label={at("partner_settings_move_up", {}, "Move up")}
          >
            <ArrowUp size={14} />
          </AdminButton>
          <AdminButton
            size="sm"
            variant="ghost"
            onclick={() => moveMethod(index, 1)}
            disabled={index === methods.length - 1}
            aria-label={at("partner_settings_move_down", {}, "Move down")}
          >
            <ArrowDown size={14} />
          </AdminButton>
          <AdminButton
            size="sm"
            variant="ghost"
            onclick={() => duplicateMethod(index)}
            aria-label={at("partner_settings_duplicate", {}, "Duplicate")}
          >
            <Copy size={14} />
          </AdminButton>
          <AdminButton
            size="sm"
            variant="dangerSoft"
            onclick={() => deleteMethod(index)}
            aria-label={at("delete", {}, "Delete")}
          >
            <Trash2 size={14} />
          </AdminButton>
        </div>
        {#if methodEditor === index}
          <div class="partner-method-editor">
            <AdminField label={at("partner_settings_method_id", {}, "Stable method ID")}>
              <Input
                class="input"
                value={method.id}
                oninput={(event) => updateMethod(index, { id: event.currentTarget.value })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_label", {}, "Display label (optional)")}>
              <Input
                class="input"
                value={method.label}
                oninput={(event) => updateMethod(index, { label: event.currentTarget.value })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_currency", {}, "Balance currency")}>
              <AdminSelect
                value={method.currency}
                items={["RUB", "USD", "EUR", "USDT"].map((value) => ({ value, label: value }))}
                ariaLabel={at("partner_settings_method_currency", {}, "Balance currency")}
                onValueChange={(currency) => updateMethod(index, { currency })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_scale", {}, "Minor-unit scale")}>
              <Input
                class="input"
                type="number"
                min="0"
                max="8"
                value={method.scale}
                oninput={(event) =>
                  updateMethod(index, { scale: Number(event.currentTarget.value) })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_minimum", {}, "Minimum")}>
              <Input
                class="input"
                type="number"
                min="1"
                value={method.minimum}
                oninput={(event) =>
                  updateMethod(index, { minimum: Number(event.currentTarget.value) })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_maximum", {}, "Maximum (optional)")}>
              <Input
                class="input"
                type="number"
                min={method.minimum}
                value={method.maximum ?? ""}
                oninput={(event) =>
                  updateMethod(index, {
                    maximum: event.currentTarget.value ? Number(event.currentTarget.value) : null,
                  })}
              />
            </AdminField>
            <AdminField label={at("partner_settings_method_enabled", {}, "Available to partners")}>
              {@render switchControl(
                method.enabled,
                at("partner_settings_method_enabled", {}, "Available to partners"),
                (checked) => updateMethod(index, { enabled: checked })
              )}
            </AdminField>
            {#if method.type === "crypto"}
              <div class="partner-network-editor">
                <div class="partner-network-editor-head">
                  <span>{at("partner_settings_networks", {}, "Allowed networks")}</span>
                  <AdminButton size="sm" onclick={() => addNetwork(index)}>
                    <Plus size={14} />{at("partner_settings_network_add", {}, "Add network")}
                  </AdminButton>
                </div>
                {#if method.networks.length}
                  <div class="partner-network-list">
                    {#each method.networks as network, networkIndex (networkIndex)}
                      <div class="partner-network-row">
                        <AdminField label={at("partner_settings_network_id", {}, "Stable ID")}>
                          <Input
                            class="input"
                            value={network.id}
                            oninput={(event) =>
                              updateNetwork(index, networkIndex, {
                                id: event.currentTarget.value,
                              })}
                          />
                        </AdminField>
                        <AdminField
                          label={at("partner_settings_network_label", {}, "Display name")}
                        >
                          <Input
                            class="input"
                            value={network.label}
                            oninput={(event) =>
                              updateNetwork(index, networkIndex, {
                                label: event.currentTarget.value,
                              })}
                          />
                        </AdminField>
                        <AdminButton
                          size="sm"
                          variant="dangerSoft"
                          aria-label={at("partner_settings_network_remove", {}, "Remove network")}
                          onclick={() => removeNetwork(index, networkIndex)}
                        >
                          <Trash2 size={14} />
                        </AdminButton>
                      </div>
                    {/each}
                  </div>
                {:else}
                  <p>{at("partner_settings_no_networks", {}, "No networks configured")}</p>
                {/if}
              </div>
            {/if}
            <div class="partner-method-preview">
              <span>
                {#if method.type === "crypto"}<Bitcoin size={18} />{:else}<CreditCard
                    size={18}
                  />{/if}
              </span>
              <div>
                <strong>{methodTitle(method)}</strong>
                <small
                  >{at(
                    "partner_settings_preview_minimum",
                    { amount: method.minimum, currency: method.currency },
                    "Minimum {amount} {currency}"
                  )}{#if method.type === "crypto" && method.networks.length}
                    · {method.networks
                      .map((network) => network.label)
                      .filter(Boolean)
                      .join(", ")}
                  {/if}</small
                >
              </div>
            </div>
          </div>
        {/if}
      </article>
    {/each}
  </div>

  {#if duplicateMethodIds.length}
    <div class="partner-settings-error">
      <TriangleAlert size={16} />{at(
        "partner_settings_duplicate_ids",
        { ids: duplicateMethodIds.join(", ") },
        "Duplicate method IDs: {ids}"
      )}
    </div>
  {/if}
  {#if cryptoInvalid}
    <div class="partner-settings-error">
      <TriangleAlert size={16} />{at(
        "partner_settings_crypto_network_error",
        {},
        "Every enabled crypto method needs at least one network."
      )}
    </div>
  {/if}
</section>

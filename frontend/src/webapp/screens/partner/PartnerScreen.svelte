<script lang="ts">
  import { onMount } from "svelte";
  import "./partnerScreen.css";
  import {
    ArrowRight,
    Check,
    CheckCircle2,
    ChevronsUpDown,
    Copy,
    Globe2,
    History,
    RefreshCw,
    Send,
    Sparkles,
    TriangleAlert,
    UsersRound,
    WalletCards,
    X,
  } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Card from "$components/ui/card.svelte";
  import Dialog from "$components/ui/dialog.svelte";
  import Input from "$components/ui/input.svelte";
  import Skeleton from "$components/ui/skeleton.svelte";
  import Textarea from "$components/ui/textarea.svelte";
  import { Select } from "$components/ui/primitives.js";
  import { StatusMessage } from "$components/patterns/webapp/index.js";
  import { sortAdminRows, type AdminSortColumn } from "$lib/admin/tableSort.js";
  import PartnerTour, { type PartnerTourStep } from "./PartnerTour.svelte";
  import PartnerActivity from "./PartnerActivity.svelte";
  import {
    partnerProgramPreview,
    type PartnerClientPreview,
    type PartnerCommissionPreview,
    type PartnerCurrency,
    type PartnerWithdrawalPreview,
    type PartnerWithdrawalMethodPreview,
  } from "$lib/webapp/previewMock/partnerProgram.js";
  import type { CopyTextAction, Translate } from "$lib/webapp/types.js";
  import { buildPartnerWithdrawalCancelPath, type ApiClient } from "$lib/webapp/publicApi.js";
  import { loadPartnerProgram, partnerPreviewMode } from "$lib/webapp/partnerProgramApi.js";

  let {
    copyText = async () => {},
    api,
    t = (key) => key,
  }: {
    copyText?: CopyTextAction;
    api?: ApiClient["api"];
    t?: Translate;
  } = $props();

  const APPLICATION_MIN = 10;
  const ACTIVITY_PAGE_SIZE = 20;
  type ActivityTab = "clients" | "commissions" | "withdrawals";

  const clientColumns: AdminSortColumn<PartnerClientPreview>[] = [
    { asc: "client_asc", desc: "client_desc", defaultDirection: "asc", value: (row) => row.label },
    {
      asc: "attributed_asc",
      desc: "attributed_desc",
      defaultDirection: "desc",
      value: (row) => row.attributedAt,
    },
    { asc: "source_asc", desc: "source_desc", defaultDirection: "asc", value: (row) => row.source },
    {
      asc: "payments_asc",
      desc: "payments_desc",
      defaultDirection: "desc",
      value: (row) => row.payments,
    },
    { asc: "gross_asc", desc: "gross_desc", defaultDirection: "desc", value: (row) => row.gross },
  ];
  const commissionColumns: AdminSortColumn<PartnerCommissionPreview>[] = [
    {
      asc: "client_asc",
      desc: "client_desc",
      defaultDirection: "asc",
      value: (row) => row.clientLabel,
    },
    {
      asc: "created_asc",
      desc: "created_desc",
      defaultDirection: "desc",
      value: (row) => row.createdAt,
    },
    { asc: "basis_asc", desc: "basis_desc", defaultDirection: "desc", value: (row) => row.gross },
    {
      asc: "amount_asc",
      desc: "amount_desc",
      defaultDirection: "desc",
      value: (row) => row.amount,
    },
    { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
  ];
  const withdrawalColumns: AdminSortColumn<PartnerWithdrawalPreview>[] = [
    { asc: "method_asc", desc: "method_desc", defaultDirection: "asc", value: (row) => row.method },
    {
      asc: "created_asc",
      desc: "created_desc",
      defaultDirection: "desc",
      value: (row) => row.createdAt,
    },
    {
      asc: "amount_asc",
      desc: "amount_desc",
      defaultDirection: "desc",
      value: (row) => row.amount,
    },
    { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
  ];
  const defaultSortByTab: Record<ActivityTab, string> = {
    clients: "gross_desc",
    commissions: "created_desc",
    withdrawals: "created_desc",
  };

  // Every step points at a real control on this screen, so the coach mark
  // explains what the partner is actually looking at.
  const tourSteps: PartnerTourStep[] = [
    {
      target: "links",
      titleKey: "wa_partner_tutorial_1_title",
      textKey: "wa_partner_tutorial_1_text",
    },
    {
      target: "balance",
      titleKey: "wa_partner_tutorial_2_title",
      textKey: "wa_partner_tutorial_2_text",
    },
    {
      target: "clients",
      titleKey: "wa_partner_tutorial_3_title",
      textKey: "wa_partner_tutorial_3_text",
    },
    {
      target: "withdraw",
      titleKey: "wa_partner_tutorial_4_title",
      textKey: "wa_partner_tutorial_4_text",
    },
  ];

  function loadPreview(): ReturnType<typeof partnerProgramPreview> {
    return partnerProgramPreview(t);
  }

  function initialApplicationError(): string {
    return initialPreview.validationError ? t("wa_partner_application_validation_error") : "";
  }

  const previewMode = partnerPreviewMode();
  const initialPreview = loadPreview();
  if (!previewMode) initialPreview.loading = true;
  let preview = $state(initialPreview);
  let activeTab = $state<ActivityTab>("clients");
  let activitySort = $state(defaultSortByTab.clients);
  let activityPage = $state(0);
  let selectedCurrency = $state<PartnerCurrency>(initialPreview.balances[0]?.currency || "RUB");
  let applicationText = $state(initialPreview.applicationMessage || "");
  let applicationBusy = $state(false);
  let applicationError = $state(initialApplicationError());
  let withdrawalOpen = $state(false);
  let withdrawalMethodId = $state(initialPreview.methods[0]?.id || "");
  let withdrawalAmount = $state("");
  let withdrawalNetwork = $state("");
  let withdrawalRequisites = $state("");
  let withdrawalBusy = $state(false);
  let withdrawalError = $state("");
  let tutorialStep = $state(initialPreview.tutorialStep);
  const applicationMax = $derived(preview.applicationMaxLength || 2000);

  function idempotencyKey(prefix: string): string {
    const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    return `${prefix}-${random}`;
  }

  async function refreshProgram(): Promise<void> {
    if (previewMode) {
      preview = loadPreview();
      return;
    }
    if (!api) {
      preview.loading = false;
      preview.error = true;
      return;
    }
    preview.loading = true;
    preview.error = false;
    try {
      preview = await loadPartnerProgram(api);
      selectedCurrency = preview.balances[0]?.currency || "RUB";
      withdrawalMethodId = preview.methods[0]?.id || "";
      applicationText = preview.applicationMessage || "";
      applicationError = "";
    } catch {
      preview.loading = false;
      preview.error = true;
    }
  }

  onMount(() => {
    if (!previewMode) void refreshProgram();
  });

  const currentBalance = $derived(
    preview.balances.find((balance) => balance.currency === selectedCurrency) ||
      preview.balances[0] ||
      null
  );
  const selectedMethod = $derived(
    preview.methods.find((method) => method.id === withdrawalMethodId) || null
  );
  const currencyMethods = $derived(
    preview.methods.filter((method) => method.currency === selectedCurrency)
  );
  const currencyClients = $derived(
    preview.clients.filter((client) => client.currency === selectedCurrency)
  );
  const currencyCommissions = $derived(
    preview.commissions.filter((commission) => commission.currency === selectedCurrency)
  );
  const currencyWithdrawals = $derived(
    preview.withdrawals.filter((withdrawal) => withdrawal.currency === selectedCurrency)
  );
  const sortedClients = $derived(sortAdminRows(currencyClients, activitySort, clientColumns));
  const sortedCommissions = $derived(
    sortAdminRows(currencyCommissions, activitySort, commissionColumns)
  );
  const sortedWithdrawals = $derived(
    sortAdminRows(currencyWithdrawals, activitySort, withdrawalColumns)
  );
  const activityTotal = $derived(
    activeTab === "clients"
      ? sortedClients.length
      : activeTab === "commissions"
        ? sortedCommissions.length
        : sortedWithdrawals.length
  );
  const activityPageCount = $derived(Math.max(1, Math.ceil(activityTotal / ACTIVITY_PAGE_SIZE)));
  const activityFrom = $derived(activityPage * ACTIVITY_PAGE_SIZE);
  const activityTo = $derived(activityFrom + ACTIVITY_PAGE_SIZE);
  const pagedClients = $derived(sortedClients.slice(activityFrom, activityTo));
  const pagedCommissions = $derived(sortedCommissions.slice(activityFrom, activityTo));
  const pagedWithdrawals = $derived(sortedWithdrawals.slice(activityFrom, activityTo));
  const activeProfile = $derived(Boolean(preview.profileState));
  const partnerPaused = $derived(
    preview.profileState !== null && preview.profileState !== "active"
  );
  const applicationValid = $derived(
    applicationText.trim().length >= APPLICATION_MIN && applicationText.length <= applicationMax
  );
  const methodMinimumMet = $derived(
    Boolean(
      selectedMethod &&
      currentBalance &&
      Number(withdrawalAmount || 0) >= selectedMethod.minimum &&
      Number(withdrawalAmount || 0) <= currentBalance.available
    )
  );
  const withdrawalValid = $derived(
    Boolean(
      methodMinimumMet &&
      withdrawalRequisites.trim() &&
      (selectedMethod?.type !== "crypto" || withdrawalNetwork)
    )
  );
  const canWithdraw = $derived(
    Boolean(
      !partnerPaused &&
      currentBalance &&
      preview.methods.some(
        (method) =>
          method.enabled &&
          method.currency === currentBalance.currency &&
          currentBalance.available >= method.minimum
      )
    )
  );
  // One message per failed precondition beats a single "minimum" hint that is
  // wrong whenever the partner simply typed more than the balance holds.
  const withdrawalAmountError = $derived.by(() => {
    if (!selectedMethod || !currentBalance || !withdrawalAmount) return "";
    const amount = Number(withdrawalAmount);
    if (!Number.isFinite(amount) || amount <= 0) return t("wa_partner_withdrawal_amount_invalid");
    if (amount < selectedMethod.minimum)
      return t("wa_partner_withdrawal_amount_error", {
        minimum: formatMoney(selectedMethod.minimum, selectedMethod.currency),
      });
    if (amount > currentBalance.available)
      return t("wa_partner_withdrawal_amount_over", {
        available: formatMoney(currentBalance.available, currentBalance.currency),
      });
    return "";
  });
  const requisitesLabel = $derived(
    selectedMethod?.type === "crypto"
      ? t("wa_partner_crypto_address")
      : selectedMethod?.type === "sbp"
        ? t("wa_partner_sbp_phone")
        : t("wa_partner_card_number")
  );
  const requisitesPlaceholder = $derived(
    selectedMethod?.type === "crypto"
      ? t("wa_partner_crypto_address_placeholder")
      : selectedMethod?.type === "sbp"
        ? t("wa_partner_sbp_phone_placeholder")
        : t("wa_partner_card_number_placeholder")
  );

  function formatMoney(amount: number, currency: PartnerCurrency): string {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      minimumFractionDigits: currency === "RUB" ? 0 : 2,
      maximumFractionDigits: currency === "RUB" ? 0 : 2,
    }).format(amount);
  }

  function formatDate(value: string): string {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
  }

  function methodLabel(method: PartnerWithdrawalMethodPreview): string {
    return t(`wa_partner_method_${method.type}`);
  }

  function commissionStatusLabel(item: PartnerCommissionPreview): string {
    return t(`wa_partner_commission_status_${item.status}`);
  }

  function tableTranslate(
    key: string,
    params: Record<string, unknown> = {},
    fallback = ""
  ): string {
    const localeKey = `admin_${key}`;
    const translated = t(localeKey, params, fallback);
    return translated === localeKey ? fallback || key : translated;
  }

  function changeActivityTab(value: string): void {
    activeTab = value as ActivityTab;
    activitySort = defaultSortByTab[activeTab];
    activityPage = 0;
  }

  function changeActivitySort(value: string): void {
    activitySort = value;
    activityPage = 0;
  }

  async function submitApplication(): Promise<void> {
    if (!applicationValid || applicationBusy) return;
    applicationBusy = true;
    applicationError = "";
    if (previewMode) {
      window.setTimeout(() => {
        preview.applicationState = "pending";
        preview.applicationMessage = applicationText.trim();
        preview.applicationSubmittedAt = new Date().toISOString();
        applicationBusy = false;
      }, 320);
      return;
    }
    try {
      await api?.("/partner/applications", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: applicationText.trim() }),
      });
      await refreshProgram();
    } catch {
      applicationError = t("wa_partner_application_send_error");
    } finally {
      applicationBusy = false;
    }
  }

  function openWithdrawal(): void {
    if (!canWithdraw) return;
    const firstMethod = preview.methods.find(
      (method) =>
        method.enabled &&
        method.currency === selectedCurrency &&
        Number(currentBalance?.available || 0) >= method.minimum
    );
    withdrawalMethodId = firstMethod?.id || preview.methods[0]?.id || "";
    withdrawalAmount = "";
    withdrawalNetwork = firstMethod?.networks?.[0] || "";
    withdrawalRequisites = "";
    withdrawalError = "";
    withdrawalOpen = true;
  }

  function openTutorial(step = 1): void {
    tutorialStep = step;
  }

  function selectWithdrawalMethod(method: PartnerWithdrawalMethodPreview): void {
    withdrawalMethodId = method.id;
    withdrawalNetwork = method.networks?.[0] || "";
    withdrawalRequisites = "";
  }

  function useMaxBalance(): void {
    withdrawalAmount = String(currentBalance?.available || 0);
  }

  async function createWithdrawal(): Promise<void> {
    if (!withdrawalValid || withdrawalBusy || !selectedMethod || !currentBalance) return;
    withdrawalBusy = true;
    withdrawalError = "";
    if (previewMode) {
      window.setTimeout(() => {
        preview.withdrawals = [
          {
            id: `WD-${Date.now().toString().slice(-4)}`,
            createdAt: new Date().toISOString(),
            method: selectedMethod.type,
            masked:
              selectedMethod.type === "crypto"
                ? `${withdrawalNetwork} ••••${withdrawalRequisites.slice(-4)}`
                : `•••• ${withdrawalRequisites.replace(/\D/g, "").slice(-4)}`,
            amount: Number(withdrawalAmount),
            currency: currentBalance.currency,
            status: "requested",
          },
          ...preview.withdrawals,
        ];
        currentBalance.available -= Number(withdrawalAmount);
        currentBalance.reserved += Number(withdrawalAmount);
        activeTab = "withdrawals";
        withdrawalBusy = false;
        withdrawalOpen = false;
      }, 320);
      return;
    }
    try {
      const scale = selectedMethod.scale ?? currentBalance.scale ?? 2;
      await api?.("/partner/withdrawals", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          method_id: selectedMethod.id,
          amount_minor: Math.round(Number(withdrawalAmount) * 10 ** scale),
          currency: currentBalance.currency,
          requisites: {
            [selectedMethod.fieldId || "requisites"]: withdrawalRequisites.trim(),
          },
          network: withdrawalNetwork || null,
          idempotency_key: idempotencyKey("withdrawal"),
        }),
      });
      activeTab = "withdrawals";
      withdrawalOpen = false;
      await refreshProgram();
    } catch {
      withdrawalError = t("wa_partner_withdrawal_create_error");
    } finally {
      withdrawalBusy = false;
    }
  }

  async function cancelWithdrawal(id: string): Promise<void> {
    const item = preview.withdrawals.find((withdrawal) => withdrawal.id === id);
    if (!item || item.status !== "requested") return;
    if (!previewMode) {
      try {
        await api?.(buildPartnerWithdrawalCancelPath(id), { method: "POST" });
        await refreshProgram();
      } catch {
        item.message = t("wa_partner_withdrawal_cancel_error");
      }
      return;
    }
    item.status = "rejected";
    item.message = t("wa_partner_withdrawal_canceled_message");
    const balance = preview.balances.find((entry) => entry.currency === item.currency);
    if (balance) {
      balance.available += item.amount;
      balance.reserved = Math.max(0, balance.reserved - item.amount);
    }
  }
</script>

<main class="content with-nav partner-page" aria-busy={preview.loading}>
  {#if preview.loading}
    <div class="partner-skeleton" role="status" aria-label={t("wa_loading")}>
      <Skeleton height="120px" />
      <Skeleton height="180px" />
      <Skeleton height="90px" />
      <Skeleton height="120px" />
    </div>
  {:else if preview.error}
    <Card class="partner-state-card partner-state-error">
      <div class="partner-state-icon partner-state-icon-rejected"><TriangleAlert size={28} /></div>
      <h2>{t("wa_partner_load_error_title")}</h2>
      <p>{t("wa_partner_load_error_hint")}</p>
      <Button onclick={refreshProgram}>
        <RefreshCw size={17} />
        {t("wa_retry")}
      </Button>
    </Card>
  {:else if !activeProfile && preview.applicationState === "none"}
    <Card class="partner-application-card">
      <div class="partner-hero-icon"><Sparkles size={28} /></div>
      <div class="partner-heading">
        <span class="partner-kicker">{t("wa_partner_program_kicker")}</span>
        <h1>{t("wa_partner_application_title")}</h1>
        <p>{t("wa_partner_application_intro")}</p>
      </div>
      <div class="partner-benefits">
        <span><CheckCircle2 size={16} />{t("wa_partner_benefit_payments")}</span>
        <span><CheckCircle2 size={16} />{t("wa_partner_benefit_balance")}</span>
        <span><CheckCircle2 size={16} />{t("wa_partner_benefit_links")}</span>
      </div>
      <label class="partner-field">
        <span>{t("wa_partner_application_message_label")}</span>
        <Textarea
          bind:value={applicationText}
          maxlength={applicationMax}
          rows={7}
          placeholder={t("wa_partner_application_placeholder")}
          aria-invalid={Boolean(applicationError)}
        />
        <span class="partner-field-foot">
          <small>{t("wa_partner_application_min_hint", { count: APPLICATION_MIN })}</small>
          <small>{applicationText.length} / {applicationMax}</small>
        </span>
      </label>
      {#if applicationError}
        <StatusMessage error>{applicationError}</StatusMessage>
      {/if}
      <Button
        class="wide"
        onclick={submitApplication}
        disabled={!applicationValid || applicationBusy}
      >
        {applicationBusy ? t("wa_partner_application_sending") : t("wa_partner_application_submit")}
        <ArrowRight size={17} />
      </Button>
    </Card>
  {:else if !activeProfile && preview.applicationState === "pending"}
    <Card class="partner-state-card">
      <div class="partner-state-icon partner-state-icon-pending"><History size={28} /></div>
      <span class="partner-status partner-status-pending">{t("wa_partner_status_pending")}</span>
      <h1>{t("wa_partner_pending_title")}</h1>
      <p>{t("wa_partner_pending_hint")}</p>
      <dl class="partner-application-summary">
        <div>
          <dt>{t("wa_partner_submitted_at")}</dt>
          <dd>{formatDate(preview.applicationSubmittedAt)}</dd>
        </div>
        <div>
          <dt>{t("wa_partner_application_message_label")}</dt>
          <dd>{preview.applicationMessage}</dd>
        </div>
      </dl>
    </Card>
  {:else if !activeProfile && preview.applicationState === "rejected"}
    <Card class="partner-state-card">
      <div class="partner-state-icon partner-state-icon-rejected"><X size={28} /></div>
      <span class="partner-status partner-status-rejected">{t("wa_partner_status_rejected")}</span>
      <h1>{t("wa_partner_rejected_title")}</h1>
      <p>{t("wa_partner_rejected_hint")}</p>
      {#if preview.decisionMessage}
        <div class="partner-decision-message">
          <strong>{t("wa_partner_decision_message")}</strong>
          <p>{preview.decisionMessage}</p>
        </div>
      {/if}
      <dl class="partner-application-summary">
        <div>
          <dt>{t("wa_partner_decided_at")}</dt>
          <dd>{formatDate(preview.applicationDecisionAt)}</dd>
        </div>
        <div>
          <dt>{t("wa_partner_application_message_label")}</dt>
          <dd>{preview.applicationMessage}</dd>
        </div>
      </dl>
      {#if preview.reapplyAllowed}
        <Button
          class="wide"
          onclick={() => {
            preview.applicationState = "none";
            applicationText = "";
          }}
        >
          {t("wa_partner_reapply")}
          <ArrowRight size={17} />
        </Button>
      {/if}
    </Card>
  {:else}
    <Card class="partner-overview-card">
      <div class="partner-overview-head">
        <div class="partner-overview-title">
          <UsersRound size={34} />
          <div>
            <strong>{t("wa_partner_profile_title")}</strong>
            <span
              class="partner-status"
              class:partner-status-pending={partnerPaused}
              class:partner-status-available={!partnerPaused}
            >
              {preview.profileState === "closed"
                ? t("wa_partner_status_closed")
                : partnerPaused
                  ? t("wa_partner_status_paused")
                  : t("wa_partner_status_active")}
            </span>
          </div>
        </div>
        <Button
          class="partner-tutorial-button"
          variant="outline"
          size="sm"
          onclick={() => openTutorial()}
          aria-label={t("wa_partner_tutorial_open")}
          title={t("wa_partner_tutorial_open")}
        >
          <Sparkles size={15} /><span>{t("wa_partner_tutorial_open")}</span>
        </Button>
      </div>
      <p class="partner-overview-copy">
        {preview.welcomeMessage || t("wa_partner_program_kicker")}
      </p>

      {#if partnerPaused}
        <div class="partner-pause-banner" role="status">
          <TriangleAlert size={18} />
          <span><strong>{t("wa_partner_paused_title")}</strong>{preview.pauseReason}</span>
        </div>
      {/if}

      <div class="partner-links-section" data-tour="links">
        <h3 class="card-heading">{t("wa_partner_links_title")}</h3>
        <div class="partner-link-list">
          {#each preview.links as link (link.id)}
            <div class="partner-link-item">
              <small class="partner-link-label">
                {#if link.id === "telegram"}<Send size={14} />{:else}<Globe2 size={14} />{/if}
                {t(`wa_partner_link_${link.id}`)}
              </small>
              <div class="copy-row referral-copy-row partner-copy-row">
                <code>{link.url}</code>
                <Button
                  class="referral-copy-button partner-copy-button"
                  size="sm"
                  onclick={() => copyText(link.url, t("wa_link_copied"))}
                  disabled={!link.enabled}
                >
                  {t("wa_copy")}<Copy size={15} />
                </Button>
              </div>
            </div>
          {/each}
        </div>
      </div>
    </Card>

    {#if currentBalance}
      <Card class="partner-balance-card" data-tour="balance">
        <div class="partner-section-head">
          <div class="partner-section-title">
            <WalletCards size={30} />
            <div>
              <strong>{t("wa_partner_balance_title")}</strong>
              <p>{t("wa_partner_balance_hint")}</p>
            </div>
          </div>
          {#if preview.balances.length > 1}
            <div
              class="partner-currency-switcher"
              role="group"
              aria-label={t("wa_partner_currency_switcher")}
            >
              {#each preview.balances as balance (balance.currency)}
                <button
                  type="button"
                  class:active={selectedCurrency === balance.currency}
                  aria-pressed={selectedCurrency === balance.currency}
                  onclick={() => (selectedCurrency = balance.currency)}>{balance.currency}</button
                >
              {/each}
            </div>
          {/if}
        </div>
        <div class="partner-balance-summary">
          <div class="partner-balance-main">
            <span>{t("wa_partner_available_balance")}</span>
            <strong class:negative={currentBalance.available < 0}
              >{formatMoney(currentBalance.available, currentBalance.currency)}</strong
            >
          </div>
          <Button onclick={openWithdrawal} disabled={!canWithdraw} data-tour="withdraw">
            <WalletCards size={17} />{t("wa_partner_withdraw")}
          </Button>
          <div class="partner-balance-breakdown">
            <div>
              <span>{t("wa_partner_balance_pending")}</span><strong
                >{formatMoney(currentBalance.pending, currentBalance.currency)}</strong
              >
            </div>
            <div>
              <span>{t("wa_partner_balance_reserved")}</span><strong
                >{formatMoney(currentBalance.reserved, currentBalance.currency)}</strong
              >
            </div>
            <div>
              <span>{t("wa_partner_balance_lifetime")}</span><strong
                >{formatMoney(currentBalance.lifetime, currentBalance.currency)}</strong
              >
            </div>
          </div>
          {#if !canWithdraw}
            <p class="partner-disabled-reason">
              {partnerPaused
                ? t("wa_partner_actions_paused")
                : t("wa_partner_withdraw_unavailable")}
            </p>
          {/if}
        </div>
      </Card>
    {/if}

    <PartnerActivity
      {t}
      commissionBps={preview.commissionBps}
      {selectedCurrency}
      {currencyClients}
      {currencyCommissions}
      {currencyWithdrawals}
      {pagedClients}
      {pagedCommissions}
      {pagedWithdrawals}
      {clientColumns}
      {commissionColumns}
      {withdrawalColumns}
      bind:activeTab
      bind:activitySort
      bind:activityPage
      {activityPageCount}
      {activityTotal}
      {changeActivityTab}
      {changeActivitySort}
      {tableTranslate}
      {formatMoney}
      {formatDate}
      {commissionStatusLabel}
      {cancelWithdrawal}
    />
  {/if}
</main>

<Dialog
  open={withdrawalOpen && Boolean(currentBalance)}
  title={t("wa_partner_withdrawal_title")}
  description={t("wa_partner_withdrawal_kicker")}
  closeLabel={t("wa_close")}
  onclose={() => (withdrawalOpen = false)}
  class="partner-withdraw-dialog"
>
  {#snippet titleIcon()}<WalletCards size={22} />{/snippet}
  {#if currentBalance}
    <div class="partner-dialog-body">
      <div class="partner-withdraw-balance">
        <span>{t("wa_partner_available_balance")}</span><strong
          >{formatMoney(currentBalance.available, currentBalance.currency)}</strong
        >
      </div>
      <div class="partner-method-options">
        {#each currencyMethods as method (method.id)}
          <button
            type="button"
            class:active={withdrawalMethodId === method.id}
            disabled={!method.enabled || currentBalance.available < method.minimum}
            onclick={() => selectWithdrawalMethod(method)}
          >
            <strong>{methodLabel(method)}</strong>
            <small
              >{t("wa_partner_method_minimum", {
                amount: formatMoney(method.minimum, method.currency),
              })}</small
            >
          </button>
        {/each}
      </div>
      <label class="partner-field">
        <span>{t("wa_partner_withdrawal_amount")}</span>
        <div class="partner-amount-row">
          <Input
            type="number"
            min={selectedMethod?.minimum || 0}
            max={currentBalance.available}
            inputmode="decimal"
            aria-invalid={Boolean(withdrawalAmountError)}
            bind:value={withdrawalAmount}
          />
          <Button variant="outline" onclick={useMaxBalance}>{t("wa_partner_max")}</Button>
        </div>
        {#if withdrawalAmountError}
          <small class="field-error">{withdrawalAmountError}</small>
        {/if}
      </label>
      {#if selectedMethod?.type === "crypto"}
        <label class="partner-field">
          <span>{t("wa_partner_crypto_network")}</span>
          <Select.Root
            type="single"
            value={withdrawalNetwork}
            items={(selectedMethod.networks || []).map((network) => ({
              value: network,
              label: network,
            }))}
            onValueChange={(value) => (withdrawalNetwork = value)}
          >
            <Select.Trigger
              class="partner-network-select-trigger"
              aria-label={t("wa_partner_crypto_network")}
            >
              <span>{withdrawalNetwork || t("wa_partner_crypto_network")}</span>
              <ChevronsUpDown size={15} />
            </Select.Trigger>
            <Select.Portal>
              <Select.Content
                class="partner-network-select-content"
                side="bottom"
                align="start"
                sideOffset={6}
              >
                <Select.Viewport class="partner-network-select-viewport">
                  {#each selectedMethod.networks || [] as network}
                    <Select.Item
                      value={network}
                      label={network}
                      class="partner-network-select-item"
                    >
                      <span>{network}</span>
                      <Check size={14} class="partner-network-select-check" />
                    </Select.Item>
                  {/each}
                </Select.Viewport>
              </Select.Content>
            </Select.Portal>
          </Select.Root>
        </label>
      {/if}
      <label class="partner-field">
        <span>{requisitesLabel}</span>
        <Input
          bind:value={withdrawalRequisites}
          autocomplete="off"
          placeholder={requisitesPlaceholder}
          inputmode={selectedMethod?.type === "sbp" ? "tel" : undefined}
        />
        <small>{t("wa_partner_requisites_privacy")}</small>
      </label>
      {#if withdrawalError}
        <StatusMessage error>{withdrawalError}</StatusMessage>
      {/if}
      <div class="partner-dialog-actions bottom-action">
        <Button variant="outline" onclick={() => (withdrawalOpen = false)}>{t("wa_cancel")}</Button>
        <Button onclick={createWithdrawal} disabled={!withdrawalValid || withdrawalBusy}>
          {withdrawalBusy ? t("wa_partner_withdrawal_creating") : t("wa_partner_withdrawal_create")}
        </Button>
      </div>
    </div>
  {/if}
</Dialog>

<PartnerTour steps={tourSteps} bind:step={tutorialStep} {t} />

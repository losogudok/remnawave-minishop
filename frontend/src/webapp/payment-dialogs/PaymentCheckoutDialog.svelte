<script lang="ts">
  import {
    ArrowLeft,
    ArrowRight,
    CheckCircle2,
    ExternalLink,
    History,
    LockKeyhole,
    Tag,
    WalletCards,
  } from "$components/ui/icons.js";

  import Button from "$components/ui/button.svelte";
  import Checkbox from "$components/ui/checkbox.svelte";
  import Dialog from "$components/ui/dialog.svelte";
  import { EmptyCard, PaymentMethodGrid } from "$components/patterns/webapp/index.js";
  import CheckoutPromoRow from "../CheckoutPromoRow.svelte";
  import {
    checkoutPromoAffectsQuotedPlan,
    checkoutPromoBlockVisible,
    selectPaymentMethodWithPromoReset,
  } from "$lib/webapp/checkoutPromoPolicy.js";
  import { formatCompactNumber, formatMoney } from "$lib/webapp/formatters.js";
  import {
    partnerBalanceCheckoutPreview,
    type PartnerBalanceCheckoutPreview,
  } from "$lib/webapp/previewMock/partnerProgram.js";
  import type { ApiClient, PartnerOverviewResponse } from "$lib/webapp/publicApi.js";
  import {
    planKey as planKeyFn,
    planDisplayTitle as planDisplayTitleFn,
    planSubtitle as planSubtitleFn,
    planUnitHint as planUnitHintFn,
    tariffLimitLabel as tariffLimitLabelFn,
    priceLabel as priceLabelFn,
    firstAvailableMethod,
    methodSelectable,
    methodsForPlan,
  } from "$lib/webapp/tariffs.js";
  import type {
    PaymentMethodView,
    PendingPaymentView,
    PlanView,
    SubscriptionView,
    TariffView,
    TermUnitLabel,
    Translate,
    VoidAction,
  } from "$lib/webapp/types.js";

  let {
    api,
    refreshData = async () => {},
    createPayment = () => {},
    hasMultipleTariffs = false,
    methods = [],
    pendingPayment = null,
    payBusy = false,
    paymentModalOpen = $bindable(false),
    paymentStep = $bindable("tariff"),
    plans = [],
    selectedMethod = $bindable(""),
    selectedPlan = $bindable(null),
    selectedTariff = null,
    selectedTariffKey = $bindable(""),
    selectedTariffPlans = [],
    renewHwidDevices = $bindable(true),
    singleTariffMode = false,
    subscription = {},
    subscriptionPurchaseDescription = "",
    tariffCatalog = [],
    tariffMode = false,
    trafficMode = false,
    closePaymentModal = () => {},
    checkoutPromoAppliedCode = "",
    checkoutPromoInput = $bindable(""),
    checkoutPromoIsError = false,
    checkoutPromoPriceText = "",
    checkoutPromoEffectiveAmount = 0,
    checkoutPromoStatus = "",
    checkoutPromoDiscountPercent = 0,
    checkoutPromoAppliesTo = "all",
    checkoutPromoMinSubscriptionMonths = null,
    checkoutPromoMinTrafficGb = null,
    applyCheckoutPromo = () => {},
    backToTariffList = () => {},
    clearCheckoutPromo = () => {},
    continueWithSelectedTariff = () => {},
    resumePendingPayment = () => {},
    selectTariff = () => {},
    t = (key) => key,
    termUnitLabel = () => "",
  }: {
    api: ApiClient["api"];
    refreshData?: () => Promise<unknown>;
    createPayment?: VoidAction;
    hasMultipleTariffs?: boolean;
    methods?: PaymentMethodView[];
    pendingPayment?: PendingPaymentView | null;
    payBusy?: boolean;
    paymentModalOpen?: boolean;
    paymentStep?: string;
    plans?: PlanView[];
    selectedMethod?: string;
    selectedPlan?: PlanView | null;
    selectedTariff?: TariffView | null;
    selectedTariffKey?: string;
    selectedTariffPlans?: PlanView[];
    renewHwidDevices?: boolean;
    singleTariffMode?: boolean;
    subscription?: SubscriptionView;
    subscriptionPurchaseDescription?: string;
    tariffCatalog?: TariffView[];
    tariffMode?: boolean;
    trafficMode?: boolean;
    closePaymentModal?: VoidAction;
    checkoutPromoAppliedCode?: string;
    checkoutPromoInput?: string;
    checkoutPromoIsError?: boolean;
    checkoutPromoPriceText?: string;
    checkoutPromoEffectiveAmount?: number;
    checkoutPromoStatus?: string;
    checkoutPromoDiscountPercent?: number;
    checkoutPromoAppliesTo?: string;
    checkoutPromoMinSubscriptionMonths?: number | null;
    checkoutPromoMinTrafficGb?: number | null;
    applyCheckoutPromo?: VoidAction;
    backToTariffList?: VoidAction;
    clearCheckoutPromo?: VoidAction;
    continueWithSelectedTariff?: VoidAction;
    resumePendingPayment?: (payment: PendingPaymentView) => void;
    selectTariff?: (tariff: TariffView) => void;
    t?: Translate;
    termUnitLabel?: TermUnitLabel;
  } = $props();

  function priceLabel(plan: PlanView | null) {
    return priceLabelFn(plan, selectedMethod);
  }
  function pendingPaymentPrice(value: unknown) {
    const amount = Number(value || 0);
    const provider = String(pendingPayment?.provider || "");
    return priceLabelFn(
      {
        price: amount,
        stars_price: provider.toLowerCase().includes("stars") ? amount : undefined,
        currency: String(pendingPayment?.currency || ""),
      },
      provider
    );
  }
  function pendingPaymentTerm() {
    const months = Number(pendingPayment?.months || 0);
    if (months > 0) return termUnitLabel(months, "month");
    const trafficGb = Number(pendingPayment?.purchased_gb || 0);
    if (trafficGb > 0) {
      return t("wa_pending_payment_traffic", { gb: formatCompactNumber(trafficGb) });
    }
    const devices = Number(pendingPayment?.purchased_hwid_devices || 0);
    if (devices > 0) return t("wa_pending_payment_devices", { count: devices });
    return t("wa_pending_payment_purchase");
  }
  function pendingPaymentDiscount() {
    const summary = String(pendingPayment?.promo_effect_summary || "").trim();
    if (summary) return summary;
    const percent = Number(pendingPayment?.discount_percent || 0);
    if (percent > 0) {
      return t("wa_pending_payment_discount_percent", {
        percent: formatCompactNumber(percent),
      });
    }
    return t("wa_pending_payment_discount_applied");
  }
  function methodUsesStars() {
    return String(selectedMethod || "")
      .toLowerCase()
      .includes("stars");
  }
  function providerManagesPrice() {
    const normalizedMethod = String(selectedMethod || "").toLowerCase();
    if (
      selectedPlan?.externally_managed_price_method_ids?.some(
        (methodId) => String(methodId).toLowerCase() === normalizedMethod
      )
    ) {
      return true;
    }
    return Boolean(
      methods.find((method) => String(method.id || "").toLowerCase() === normalizedMethod)
        ?.price_managed_externally
    );
  }
  function tributeShopSubscriptionSelected(methodId = selectedMethod) {
    return (
      String(methodId || "").toLowerCase() === "tribute" &&
      !providerManagesPrice() &&
      isSubscriptionPlan(selectedPlan)
    );
  }
  function selectPaymentMethod(methodId: string) {
    selectPaymentMethodWithPromoReset(
      methodId,
      selectedPlan,
      (nextMethod) => (selectedMethod = nextMethod),
      clearCheckoutPromo
    );
  }
  function hwidRenewalFor(plan: PlanView | null) {
    return plan?.hwid_renewal?.available ? plan.hwid_renewal : null;
  }
  function isSubscriptionPlan(plan: PlanView | null) {
    const saleMode = String(plan?.sale_mode || "subscription").toLowerCase();
    return saleMode === "subscription";
  }
  function hwidRenewalAvailableForMethod(plan: PlanView | null) {
    const renewal = hwidRenewalFor(plan);
    if (
      providerManagesPrice() ||
      tributeShopSubscriptionSelected() ||
      !subscription?.active ||
      !isSubscriptionPlan(plan) ||
      !renewal
    ) {
      return false;
    }
    if (methodUsesStars()) return Number(renewal.stars_price || 0) > 0;
    return Number(renewal.price || 0) > 0;
  }
  function planWithSelectedHwidRenewal(plan: PlanView | null) {
    if (!plan || !renewHwidDevices || !hwidRenewalAvailableForMethod(plan)) return plan;
    const renewal = hwidRenewalFor(plan);
    if (!renewal) return plan;
    const withRenewal: PlanView = {
      ...plan,
      price: Number(plan.price || 0) + Number(renewal.price || 0),
    };
    if (Number(plan.stars_price || 0) > 0 && Number(renewal.stars_price || 0) > 0) {
      withRenewal.stars_price = Number(plan.stars_price || 0) + Number(renewal.stars_price || 0);
    }
    return withRenewal;
  }
  function paymentPriceLabel(plan: PlanView | null) {
    return priceLabelFn(planWithSelectedHwidRenewal(plan), selectedMethod);
  }
  function checkoutPaymentPriceLabel(plan: PlanView | null) {
    if (providerManagesPrice()) return t("wa_price_managed_by_provider");
    const promoPrice = checkoutPromoPriceParts(planWithSelectedHwidRenewal(plan));
    if (promoPrice) return promoPrice.discounted;
    if (checkoutPromoAppliedCode && checkoutPromoPriceText) return checkoutPromoPriceText;
    return paymentPriceLabel(plan);
  }
  function checkoutPromoDiscount() {
    const value = Number(checkoutPromoDiscountPercent || 0);
    if (!checkoutPromoAppliedCode || !Number.isFinite(value) || value <= 0) return 0;
    return Math.min(100, value);
  }
  function planSaleModeBase(plan: PlanView | null) {
    const fallback =
      Number(plan?.device_count || 0) > 0
        ? "hwid_devices"
        : Number(plan?.traffic_gb || 0) > 0
          ? "traffic"
          : "subscription";
    const saleMode = String(plan?.sale_mode || fallback).toLowerCase();
    if (["traffic", "traffic_package"].includes(saleMode)) return "traffic";
    if (["topup", "premium_topup"].includes(saleMode)) return "traffic_topup";
    if (["hwid_device", "hwid_devices", "hwid_devices_renewal"].includes(saleMode)) return "hwid";
    return "subscription";
  }
  function checkoutPromoScopeMatches(plan: PlanView | null) {
    const scope = String(checkoutPromoAppliesTo || "all").toLowerCase();
    const base = planSaleModeBase(plan);
    return scope === "all" || scope === base;
  }
  function checkoutPromoThresholdMatches(plan: PlanView | null) {
    const base = planSaleModeBase(plan);
    const minMonths = Number(checkoutPromoMinSubscriptionMonths || 0);
    const minTrafficGb = Number(checkoutPromoMinTrafficGb || 0);
    if (base === "subscription" && minMonths > 0) {
      return Number(plan?.months || 0) >= minMonths;
    }
    if ((base === "traffic" || base === "traffic_topup") && minTrafficGb > 0) {
      return Number(plan?.traffic_gb || plan?.months || 0) >= minTrafficGb;
    }
    return true;
  }
  function checkoutPromoAffectsPlan(plan: PlanView | null) {
    return checkoutPromoAffectsQuotedPlan(
      checkoutPromoDiscount(),
      checkoutPromoScopeMatches(plan),
      checkoutPromoThresholdMatches(plan)
    );
  }
  function discountedCheckoutPlan(plan: PlanView | null) {
    const discount = checkoutPromoDiscount();
    if (!plan || discount <= 0) return plan;
    const multiplier = Math.max(0, 1 - discount / 100);
    const next: PlanView = { ...plan };
    if (Number(plan.price || 0) > 0) {
      next.price = Math.round(Number(plan.price || 0) * multiplier * 100) / 100;
    }
    if (Number(plan.stars_price || 0) > 0) {
      next.stars_price = Math.max(1, Math.round(Number(plan.stars_price || 0) * multiplier));
    }
    return next;
  }
  function checkoutPromoPriceParts(plan: PlanView | null) {
    if (!checkoutPromoAffectsPlan(plan)) return null;
    return {
      base: priceLabelFn(plan, selectedMethod),
      discounted: priceLabelFn(discountedCheckoutPlan(plan), selectedMethod),
    };
  }
  const selectedPlanForPayment = $derived(planWithSelectedHwidRenewal(selectedPlan));
  const paymentMethods = $derived(methodsForPlan(methods, selectedPlanForPayment));
  const paymentMethodSelected = $derived(methodSelectable(paymentMethods, selectedMethod));

  $effect(() => {
    if (!paymentModalOpen || paymentStep !== "checkout" || !selectedPlan) return;
    const firstMethod = firstAvailableMethod(paymentMethods);
    if (firstMethod && !methodSelectable(paymentMethods, selectedMethod)) {
      selectedMethod = firstMethod;
    }
  });
  function hwidRenewalPriceLabel(plan: PlanView | null = selectedPlan) {
    const renewal = hwidRenewalFor(plan);
    if (!renewal) return "";
    return priceLabelFn(
      {
        price: renewal.price || 0,
        stars_price: renewal.stars_price,
        currency: renewal.currency || plan?.currency,
      },
      selectedMethod
    );
  }
  function showHwidRenewalBlock() {
    return hwidRenewalAvailableForMethod(selectedPlan);
  }
  function showHwidRenewalUnavailableNote() {
    return Boolean(
      subscription?.active &&
      Number(subscription?.extra_hwid_devices || 0) > 0 &&
      isSubscriptionPlan(selectedPlan) &&
      !showHwidRenewalBlock()
    );
  }
  function hwidRenewalCount(plan: PlanView | null = selectedPlan) {
    return Number(hwidRenewalFor(plan)?.device_count || subscription?.extra_hwid_devices || 0);
  }
  function hwidRenewalBonusLabel(plan: PlanView | null = selectedPlan) {
    const renewal = hwidRenewalFor(plan);
    const bonusGb = Number(renewal?.traffic_bonus_gb || 0);
    if (!(bonusGb > 0)) return "";
    return t("wa_hwid_devices_traffic_bonus", { gb: formatCompactNumber(bonusGb) });
  }
  function hwidRenewalHint(plan: PlanView | null = selectedPlan) {
    const renewal = hwidRenewalFor(plan);
    if (renewal?.valid_from_text && renewal?.valid_until_text) {
      return t("wa_hwid_devices_renewal_checkbox_hint", {
        from: renewal.valid_from_text,
        to: renewal.valid_until_text,
      });
    }
    return t("wa_hwid_devices_renewal_checkbox_hint_short");
  }
  function showHwidDesyncNotice() {
    return Boolean(
      subscription?.device_topup_renewal_available &&
      subscription?.extra_hwid_devices_valid_until_text
    );
  }
  function planKey(plan: PlanView | null) {
    return planKeyFn(plan);
  }
  function planDisplayTitle(plan: PlanView | null) {
    return planDisplayTitleFn(plan, { trafficMode, t });
  }
  function planSubtitle(plan: PlanView | null) {
    return planSubtitleFn(plan, { t, termUnitLabel });
  }
  function planUnitHint(plan: PlanView | null) {
    return planUnitHintFn(plan, { trafficMode, selectedMethod, t });
  }
  function tariffLimitLabel(tariff: TariffView) {
    return tariffLimitLabelFn(tariff, { t });
  }

  function checkoutPromoBlock() {
    return checkoutPromoBlockVisible(
      providerManagesPrice(),
      Boolean(checkoutPromoAppliedCode || checkoutPromoStatus || selectedPlan)
    );
  }

  function paymentTitle() {
    if (singleTariffMode) {
      return selectedTariff?.billing_model === "traffic"
        ? t("wa_traffic_packages_title")
        : t("wa_subscription_title");
    }
    if (tariffMode) return t("wa_tariffs_title");
    return trafficMode ? t("wa_traffic_packages_title") : t("wa_subscription_title");
  }

  function paymentDescription() {
    if (tariffMode) {
      if (singleTariffMode) {
        return selectedTariff?.billing_model === "traffic"
          ? t("wa_traffic_packages_choose")
          : t("wa_subscription_choose_period");
      }
      return paymentStep === "checkout" && selectedTariff
        ? t("wa_tariff_choose_period_payment", { tariff: selectedTariff.title })
        : t("wa_tariffs_choose");
    }
    return trafficMode ? t("wa_traffic_packages_choose") : t("wa_subscription_choose_period");
  }

  function showSubscriptionPurchaseDescription() {
    if (!subscriptionPurchaseDescription || trafficMode) return false;
    if (!tariffMode) return true;
    if (paymentStep === "tariff") return false;
    return String(selectedTariff?.billing_model || "period").toLowerCase() !== "traffic";
  }
  const initialPartnerBalancePreview = partnerBalanceCheckoutPreview();
  const partnerCheckoutPreviewMode =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("partner_checkout");
  let partnerBalancePreview = $state<PartnerBalanceCheckoutPreview | null>(
    initialPartnerBalancePreview
  );
  let partnerBalanceSpent = $state(false);
  let partnerBalanceBusy = $state(false);
  let partnerBalanceError = $state("");
  let partnerBalanceRequestKey = "";

  function balancePaymentContext() {
    const plan = selectedPlan;
    const tariffKey = String(plan?.tariff_key || selectedTariffKey || selectedTariff?.key || "");
    const months = Number(plan?.months || 0);
    const currency = String(plan?.currency || "").toUpperCase();
    const quotedPromoAmount = Number(checkoutPromoEffectiveAmount || 0);
    const due =
      checkoutPromoAppliedCode && quotedPromoAmount > 0
        ? quotedPromoAmount
        : Number(plan?.price || 0);
    const subscriptionTariff = String(subscription?.tariff_key || "");
    const eligible = Boolean(
      plan &&
      subscription?.active &&
      months > 0 &&
      due > 0 &&
      currency &&
      tariffKey &&
      (!subscriptionTariff || subscriptionTariff === tariffKey) &&
      String(plan.sale_mode || "subscription").startsWith("subscription") &&
      !trafficMode
    );
    return { plan, tariffKey, months, currency, due, eligible };
  }

  async function loadPartnerBalanceOption(): Promise<void> {
    if (partnerCheckoutPreviewMode || !paymentModalOpen) return;
    const context = balancePaymentContext();
    if (!context.eligible) {
      partnerBalancePreview = null;
      return;
    }
    const requestKey = [
      context.tariffKey,
      context.months,
      context.currency,
      checkoutPromoAppliedCode,
    ].join(":");
    partnerBalanceRequestKey = requestKey;
    try {
      const overview = (await api("/partner/overview")) as PartnerOverviewResponse;
      if (partnerBalanceRequestKey !== requestKey) return;
      const balance = overview.balances.find((item) => item.currency === context.currency);
      if (!overview.balance_payment_enabled || overview.profile?.status !== "active" || !balance) {
        partnerBalancePreview = null;
        return;
      }
      const available = balance.available_minor / 10 ** balance.currency_scale;
      const shortage = Math.max(0, context.due - available);
      partnerBalancePreview = {
        available: formatMoney(available, context.currency),
        due: formatMoney(context.due, context.currency),
        shortage: shortage ? formatMoney(shortage, context.currency) : "",
        enabled: shortage === 0 && available >= 0,
        reasonKey:
          available < 0 ? "wa_partner_balance_negative" : "wa_partner_balance_insufficient",
      };
    } catch {
      partnerBalancePreview = null;
    }
  }

  async function payWithPartnerBalance(): Promise<void> {
    if (partnerCheckoutPreviewMode) {
      partnerBalanceSpent = true;
      return;
    }
    const context = balancePaymentContext();
    if (!partnerBalancePreview?.enabled || !context.eligible || partnerBalanceBusy) return;
    partnerBalanceBusy = true;
    partnerBalanceError = "";
    try {
      const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
      await api("/partner/balance/renew", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tariff_key: context.tariffKey,
          months: context.months,
          promo_code: checkoutPromoAppliedCode || null,
          idempotency_key: `balance-${random}`,
        }),
      });
      partnerBalanceSpent = true;
      await refreshData();
      closePaymentModal();
    } catch {
      partnerBalanceError = t("wa_partner_balance_checkout_error");
      await loadPartnerBalanceOption();
    } finally {
      partnerBalanceBusy = false;
    }
  }

  $effect(() => {
    paymentModalOpen;
    selectedPlan;
    selectedTariffKey;
    checkoutPromoAppliedCode;
    if (!partnerCheckoutPreviewMode) void loadPartnerBalanceOption();
  });
</script>

{#snippet partnerBalanceOption()}
  {#if partnerBalancePreview}
    <section class="partner-balance-checkout" class:disabled={!partnerBalancePreview.enabled}>
      <div class="partner-balance-checkout-icon"><WalletCards size={19} /></div>
      <div class="partner-balance-checkout-copy">
        <strong>{t("wa_partner_balance_checkout_title")}</strong>
        <span
          >{t("wa_partner_balance_checkout_summary", {
            balance: partnerBalancePreview.available,
            due: partnerBalancePreview.due,
          })}</span
        >
        {#if partnerBalancePreview.shortage}<small
            >{t(partnerBalancePreview.reasonKey, {
              shortage: partnerBalancePreview.shortage,
            })}</small
          >{/if}
        {#if partnerBalanceError}<small>{partnerBalanceError}</small>{/if}
      </div>
      <Button
        variant="outline"
        size="sm"
        disabled={!partnerBalancePreview.enabled || partnerBalanceSpent || partnerBalanceBusy}
        onclick={payWithPartnerBalance}
      >
        {partnerBalanceSpent
          ? t("wa_partner_balance_checkout_confirmed")
          : t("wa_partner_balance_checkout_action")}
      </Button>
    </section>
  {/if}
{/snippet}

<Dialog
  open={paymentModalOpen}
  title={paymentTitle()}
  description={paymentDescription()}
  closeLabel={t("wa_close")}
  onclose={closePaymentModal}
  class="payment-dialog-card webapp-payment-dialog"
>
  <div class="payment-dialog-body">
    {#if pendingPayment}
      <section class="pending-payment-card">
        <div class="pending-payment-heading">
          <span class="pending-payment-icon"><History size={18} /></span>
          <span>
            <strong>{t("wa_pending_payment_title")}</strong>
            <small>
              {t("wa_pending_payment_description", {
                promo: pendingPayment.promo_code || "",
              })}
            </small>
          </span>
        </div>
        <div class="pending-payment-facts">
          <span>
            <small>{t("wa_pending_payment_term")}</small>
            <strong>{pendingPaymentTerm()}</strong>
          </span>
          <span>
            <small>{t("wa_pending_payment_amount")}</small>
            <strong class="pending-payment-price">
              {#if Number(pendingPayment.base_amount || 0) > Number(pendingPayment.amount || 0)}
                <s>{pendingPaymentPrice(pendingPayment.base_amount)}</s>
              {/if}
              {pendingPaymentPrice(pendingPayment.amount)}
            </strong>
          </span>
          <span>
            <small><Tag size={12} /> {t("wa_pending_payment_discount")}</small>
            <strong>{pendingPaymentDiscount()}</strong>
          </span>
        </div>
        <Button
          class="wide pending-payment-action"
          onclick={() => resumePendingPayment(pendingPayment)}
          disabled={payBusy}
        >
          {t("wa_pending_payment_continue")}
          <ExternalLink size={16} />
        </Button>
      </section>
    {/if}
    {#if tariffMode && !singleTariffMode && paymentStep === "tariff"}
      {#if tariffCatalog.length}
        <div class="option-list tariff-list">
          {#each tariffCatalog as tariff}
            <button
              class:active={selectedTariffKey === tariff.key}
              class="option-row tariff-row"
              type="button"
              onclick={() => selectTariff(tariff)}
            >
              <span class="option-row-main">
                <strong>{tariff.title}</strong>
                <small>{tariff.description || t("wa_tariff_no_description")}</small>
              </span>
              <span class="option-row-meta">
                <em>{tariffLimitLabel(tariff)}</em>
                {#if selectedTariffKey === tariff.key}
                  <CheckCircle2 size={18} />
                {:else}
                  <ArrowRight size={17} />
                {/if}
              </span>
            </button>
          {/each}
        </div>
        <Button
          class="wide bottom-action payment-submit-button"
          onclick={continueWithSelectedTariff}
          disabled={!selectedTariffKey}
        >
          {t("wa_next")}
          <ArrowRight size={17} />
        </Button>
      {:else}
        <EmptyCard>{t("wa_no_tariff_change_options")}</EmptyCard>
      {/if}
    {:else if tariffMode}
      {#if !singleTariffMode && !(subscription?.active && subscription?.tariff_key && tariffCatalog.some((t) => t.key === subscription.tariff_key))}
        <button class="back-inline" type="button" onclick={backToTariffList}>
          <ArrowLeft size={16} />
          {t("wa_back_to_tariffs")}
        </button>
      {/if}
      {#if hasMultipleTariffs && selectedTariff}
        <p class="tariff-step-caption">
          {t("wa_selected_tariff", { tariff: selectedTariff.title })}
        </p>
      {/if}
      {#if selectedTariffPlans.length}
        {#if showSubscriptionPurchaseDescription()}
          <div class="subscription-purchase-description">
            <p>{subscriptionPurchaseDescription}</p>
          </div>
        {/if}
        {#if showHwidRenewalBlock()}
          <label class="hwid-renewal-option">
            <Checkbox
              checked={renewHwidDevices}
              ariaLabel={t("wa_hwid_devices_renewal_checkbox_aria")}
              onCheckedChange={(checked) => (renewHwidDevices = checked)}
            />
            <span>
              <strong>
                {t("wa_hwid_devices_renewal_checkbox", {
                  count: hwidRenewalCount(),
                  price: hwidRenewalPriceLabel(),
                })}
              </strong>
              <small>{hwidRenewalHint()}</small>
              {#if hwidRenewalBonusLabel()}
                <small class="hwid-traffic-bonus">{hwidRenewalBonusLabel()}</small>
              {/if}
              {#if showHwidDesyncNotice()}
                <small class="hwid-renewal-warning">
                  {t("wa_hwid_devices_desync_notice", {
                    date: subscription.extra_hwid_devices_valid_until_text,
                  })}
                </small>
              {/if}
            </span>
          </label>
        {:else if showHwidRenewalUnavailableNote()}
          <div class="subscription-purchase-description">
            <p>
              {t("wa_hwid_devices_renewal_unavailable", {
                count: Number(subscription.extra_hwid_devices || 0),
                date: subscription.extra_hwid_devices_valid_until_text || "",
              })}
            </p>
          </div>
        {/if}
        <div class="period-grid period-grid-two-columns">
          {#each selectedTariffPlans as plan}
            {@const promoPrice = checkoutPromoPriceParts(plan)}
            <button
              class:active={planKey(selectedPlan) === planKey(plan)}
              class="period-card"
              type="button"
              onclick={() => (selectedPlan = plan)}
            >
              <strong>{planSubtitle(plan) || planDisplayTitle(plan)}</strong>
              {#if promoPrice}
                <span class="promo-price-pair">
                  <s>{promoPrice.base}</s>
                  <b>{promoPrice.discounted}</b>
                </span>
              {:else}
                <span>{priceLabel(plan)}</span>
              {/if}
              {#if planUnitHint(plan)}
                <small>{planUnitHint(plan)}</small>
              {/if}
              {#if planKey(selectedPlan) === planKey(plan)}
                <CheckCircle2 size={18} />
              {/if}
            </button>
          {/each}
        </div>
        <div class="payment-divider" aria-hidden="true"></div>
        {#if methods.length}
          <PaymentMethodGrid
            methods={paymentMethods}
            {selectedMethod}
            {t}
            onSelect={selectPaymentMethod}
          />
        {:else}
          <EmptyCard>{t("wa_payment_methods_not_configured")}</EmptyCard>
        {/if}
        {#if checkoutPromoBlock()}
          <CheckoutPromoRow
            bind:value={checkoutPromoInput}
            appliedCode={checkoutPromoAppliedCode}
            isError={checkoutPromoIsError}
            status={checkoutPromoStatus}
            onApply={applyCheckoutPromo}
            onClear={clearCheckoutPromo}
            {t}
          />
        {/if}
        {@render partnerBalanceOption()}
        <Button
          class="wide bottom-action payment-submit-button"
          onclick={createPayment}
          disabled={!selectedPlan || !paymentMethodSelected || payBusy}
        >
          {t("wa_pay")}
          {selectedPlan ? checkoutPaymentPriceLabel(selectedPlan) : ""}
          <LockKeyhole size={17} />
        </Button>
      {:else}
        <EmptyCard>{t("wa_no_tariff_change_options")}</EmptyCard>
      {/if}
    {:else}
      <!--
        Legacy / non-tariff mode (no JSON tariffs catalog OR traffic-only).
        Previously this block was also reached *in addition* to the tariff
        branch above, so users on legacy mode saw the period grid, payment
        method grid and pay button duplicated.
      -->
      {#if showSubscriptionPurchaseDescription()}
        <div class="subscription-purchase-description">
          <p>{subscriptionPurchaseDescription}</p>
        </div>
      {/if}
      {#if showHwidRenewalBlock()}
        <label class="hwid-renewal-option">
          <Checkbox
            checked={renewHwidDevices}
            ariaLabel={t("wa_hwid_devices_renewal_checkbox_aria")}
            onCheckedChange={(checked) => (renewHwidDevices = checked)}
          />
          <span>
            <strong>
              {t("wa_hwid_devices_renewal_checkbox", {
                count: hwidRenewalCount(),
                price: hwidRenewalPriceLabel(),
              })}
            </strong>
            <small>{hwidRenewalHint()}</small>
            {#if hwidRenewalBonusLabel()}
              <small class="hwid-traffic-bonus">{hwidRenewalBonusLabel()}</small>
            {/if}
            {#if showHwidDesyncNotice()}
              <small class="hwid-renewal-warning">
                {t("wa_hwid_devices_desync_notice", {
                  date: subscription.extra_hwid_devices_valid_until_text,
                })}
              </small>
            {/if}
          </span>
        </label>
      {:else if showHwidRenewalUnavailableNote()}
        <div class="subscription-purchase-description">
          <p>
            {t("wa_hwid_devices_renewal_unavailable", {
              count: Number(subscription.extra_hwid_devices || 0),
              date: subscription.extra_hwid_devices_valid_until_text || "",
            })}
          </p>
        </div>
      {/if}
      <div class="period-grid period-grid-two-columns">
        {#each plans as plan}
          {@const promoPrice = checkoutPromoPriceParts(plan)}
          <button
            class:active={planKey(selectedPlan) === planKey(plan)}
            class="period-card"
            type="button"
            onclick={() => (selectedPlan = plan)}
          >
            <strong>{planDisplayTitle(plan)}</strong>
            {#if planSubtitle(plan)}
              <em>{planSubtitle(plan)}</em>
            {/if}
            {#if promoPrice}
              <span class="promo-price-pair">
                <s>{promoPrice.base}</s>
                <b>{promoPrice.discounted}</b>
              </span>
            {:else}
              <span>{priceLabel(plan)}</span>
            {/if}
            {#if planUnitHint(plan)}
              <small>{planUnitHint(plan)}</small>
            {/if}
            {#if planKey(selectedPlan) === planKey(plan)}
              <CheckCircle2 size={18} />
            {/if}
          </button>
        {/each}
      </div>
      <div class="payment-divider" aria-hidden="true"></div>
      {#if methods.length}
        <PaymentMethodGrid
          methods={paymentMethods}
          {selectedMethod}
          {t}
          onSelect={selectPaymentMethod}
        />
      {:else}
        <EmptyCard>{t("wa_payment_methods_not_configured")}</EmptyCard>
      {/if}
      {#if checkoutPromoBlock()}
        <CheckoutPromoRow
          bind:value={checkoutPromoInput}
          appliedCode={checkoutPromoAppliedCode}
          isError={checkoutPromoIsError}
          status={checkoutPromoStatus}
          onApply={applyCheckoutPromo}
          onClear={clearCheckoutPromo}
          {t}
        />
      {/if}
      {@render partnerBalanceOption()}
      <Button
        class="wide bottom-action payment-submit-button"
        onclick={createPayment}
        disabled={!selectedPlan || !paymentMethodSelected || payBusy}
      >
        {t("wa_pay")}
        {selectedPlan ? checkoutPaymentPriceLabel(selectedPlan) : ""}
        <LockKeyhole size={17} />
      </Button>
    {/if}
  </div>
</Dialog>

<style>
  .partner-balance-checkout {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    padding: 12px;
    border: 1px solid color-mix(in srgb, var(--accent) 36%, var(--border));
    border-radius: 13px;
    background: color-mix(in srgb, var(--accent) 10%, var(--panel-2));
  }

  .partner-balance-checkout.disabled {
    border-color: var(--border);
    background: var(--surface-muted);
  }

  .partner-balance-checkout-icon {
    width: 38px;
    height: 38px;
    display: grid;
    place-items: center;
    border-radius: 11px;
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 14%, var(--panel));
  }

  .partner-balance-checkout-copy {
    min-width: 0;
    display: grid;
    gap: 3px;
  }

  .partner-balance-checkout-copy span,
  .partner-balance-checkout-copy small {
    color: var(--muted);
    font-size: 12px;
  }

  .partner-balance-checkout-copy small {
    color: var(--warning-text);
  }

  @media (max-width: 480px) {
    .partner-balance-checkout {
      grid-template-columns: auto 1fr;
    }

    .partner-balance-checkout :global(.btn) {
      grid-column: 1 / -1;
      width: 100%;
    }
  }
</style>

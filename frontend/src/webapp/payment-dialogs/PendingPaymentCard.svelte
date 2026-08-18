<script lang="ts">
  import Button from "$components/ui/button.svelte";
  import { ExternalLink, History, Tag } from "$components/ui/icons.js";
  import { formatCompactNumber } from "$lib/webapp/formatters.js";
  import { priceLabel } from "$lib/webapp/tariffs.js";
  import type { PendingPaymentView, TermUnitLabel, Translate } from "$lib/webapp/types.js";

  let {
    payment,
    payBusy = false,
    resume = () => {},
    t = (key) => key,
    termUnitLabel = () => "",
  }: {
    payment: PendingPaymentView;
    payBusy?: boolean;
    resume?: (payment: PendingPaymentView) => void;
    t?: Translate;
    termUnitLabel?: TermUnitLabel;
  } = $props();

  function paymentPrice(value: unknown): string {
    const amount = Number(value || 0);
    const provider = String(payment.provider || "");
    return priceLabel(
      {
        price: amount,
        stars_price: provider.toLowerCase().includes("stars") ? amount : undefined,
        currency: String(payment.currency || ""),
      },
      provider
    );
  }

  function paymentTerm(): string {
    const months = Number(payment.months || 0);
    if (months > 0) return termUnitLabel(months, "month");
    const trafficGb = Number(payment.purchased_gb || 0);
    if (trafficGb > 0) {
      return t("wa_pending_payment_traffic", { gb: formatCompactNumber(trafficGb) });
    }
    const devices = Number(payment.purchased_hwid_devices || 0);
    if (devices > 0) return t("wa_pending_payment_devices", { count: devices });
    return t("wa_pending_payment_purchase");
  }

  function paymentDiscount(): string {
    const summary = String(payment.promo_effect_summary || "").trim();
    if (summary) return summary;
    const percent = Number(payment.discount_percent || 0);
    return percent > 0
      ? t("wa_pending_payment_discount_percent", {
          percent: formatCompactNumber(percent),
        })
      : t("wa_pending_payment_discount_applied");
  }
</script>

<section class="pending-payment-card">
  <div class="pending-payment-heading">
    <span class="pending-payment-icon"><History size={18} /></span>
    <span>
      <strong>{t("wa_pending_payment_title")}</strong>
      <small>
        {t("wa_pending_payment_description", {
          promo: payment.promo_code || "",
        })}
      </small>
    </span>
  </div>
  <div class="pending-payment-facts">
    <span>
      <small>{t("wa_pending_payment_term")}</small>
      <strong>{paymentTerm()}</strong>
    </span>
    <span>
      <small>{t("wa_pending_payment_amount")}</small>
      <strong class="pending-payment-price">
        {#if Number(payment.base_amount || 0) > Number(payment.amount || 0)}
          <s>{paymentPrice(payment.base_amount)}</s>
        {/if}
        {paymentPrice(payment.amount)}
      </strong>
    </span>
    <span>
      <small><Tag size={12} /> {t("wa_pending_payment_discount")}</small>
      <strong>{paymentDiscount()}</strong>
    </span>
  </div>
  <Button class="wide pending-payment-action" onclick={() => resume(payment)} disabled={payBusy}>
    {t("wa_pending_payment_continue")}
    <ExternalLink size={16} />
  </Button>
</section>

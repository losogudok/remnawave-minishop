import type { PendingPaymentView, WebappRecord } from "./types.js";

export type BillingPaymentResponse = WebappRecord & {
  action?: string;
  ok: boolean;
  payment_id?: string | number;
  payment_url?: string | null;
};

type PaymentSuccessContext = WebappRecord & {
  initialSubscriptionPayment?: boolean;
  paymentId?: string | number;
  renewalSubscriptionPayment?: boolean;
};

export function createPaymentResponseHandler({
  notifyOpened,
  openExternalLink,
  openTelegramInvoice,
  startPaymentStatusPolling,
}: {
  notifyOpened: (resumed: boolean) => void;
  openExternalLink: (url: string) => void;
  openTelegramInvoice: (url: string, context: PaymentSuccessContext) => Promise<boolean>;
  startPaymentStatusPolling: (
    paymentId: string | number | undefined,
    context: PaymentSuccessContext
  ) => void;
}) {
  return async function handlePaymentResponse(
    response: BillingPaymentResponse,
    successContext: PaymentSuccessContext = {},
    closeModal: () => void = () => {},
    resumed = false
  ): Promise<boolean> {
    if (!response.ok) throw response;
    notifyOpened(resumed);
    if (response.action === "open_invoice") {
      if (!response.payment_url) throw response;
      const opened = await openTelegramInvoice(response.payment_url, successContext);
      if (!opened) return false;
    } else if (response.action === "invoice_sent") {
      startPaymentStatusPolling(response.payment_id, successContext);
      closeModal();
      return true;
    } else {
      if (!response.payment_url) throw response;
      openExternalLink(response.payment_url);
    }
    startPaymentStatusPolling(response.payment_id, successContext);
    closeModal();
    return true;
  };
}

export function createPendingPaymentResume({
  closeModal,
  getPaymentStartedWithActiveSubscription,
  handlePaymentResponse,
  isBusy,
  onError,
  rememberPending,
  setBusy,
}: {
  closeModal: () => void;
  getPaymentStartedWithActiveSubscription: () => boolean;
  handlePaymentResponse: ReturnType<typeof createPaymentResponseHandler>;
  isBusy: () => boolean;
  onError: (error: unknown) => void;
  rememberPending: (context: PaymentSuccessContext) => void;
  setBusy: (busy: boolean) => void;
}) {
  return async function resumePendingPayment(payment: PendingPaymentView): Promise<void> {
    const paymentUrl = String(payment.payment_url || "").trim();
    const paymentId = payment.payment_id;
    if (!paymentUrl || !paymentId || isBusy()) return;

    setBusy(true);
    const activeSubscription = getPaymentStartedWithActiveSubscription();
    const subscriptionPayment =
      (String(payment.sale_mode || "").split("@", 1)[0] || "subscription") === "subscription";
    const successContext = {
      paymentId,
      initialSubscriptionPayment: !activeSubscription && subscriptionPayment,
      renewalSubscriptionPayment: activeSubscription && subscriptionPayment,
    };
    try {
      rememberPending(successContext);
      await handlePaymentResponse(
        {
          ok: true,
          action: String(payment.provider || "")
            .toLowerCase()
            .includes("stars")
            ? "open_invoice"
            : "open_url",
          payment_id: paymentId,
          payment_url: paymentUrl,
        },
        successContext,
        closeModal,
        true
      );
    } catch (error: unknown) {
      onError(error);
    } finally {
      setBusy(false);
    }
  };
}

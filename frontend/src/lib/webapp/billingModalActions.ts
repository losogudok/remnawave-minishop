import type { BillingPlan, PaymentMethod, TariffCatalogEntry } from "./tariffs";
import type { SubscriptionView } from "./types";

type BillingModalStore = {
  closeDeviceTopupModal: () => void;
  openDeviceTopupModal: (defaultMethod?: string) => void;
  openPaymentModal: (
    tariffMode: boolean,
    singleTariffMode: boolean,
    tariffCatalog: TariffCatalogEntry[],
    subscription: SubscriptionView,
    plans: BillingPlan[],
    defaultMethod?: string,
    options?: Record<string, unknown>
  ) => void;
  openTariffChangeModal: (defaultMethod?: string) => void;
  openTopupModal: (kind?: string, defaultMethod?: string) => void;
};

type DevicesActionsStore = {
  disconnectDevice: (devicesEnabled: boolean) => unknown;
  loadDevices: (devicesEnabled: boolean, force?: boolean) => unknown;
};

type BillingModalActionDeps = {
  billingStore: BillingModalStore;
  devicesEnabled: () => boolean;
  devicesStore: DevicesActionsStore;
  methods: () => PaymentMethod[];
  plans: () => BillingPlan[];
  suggestedPromoCode: () => string;
  singleTariffMode: () => boolean;
  subscription: () => SubscriptionView;
  tariffCatalog: () => TariffCatalogEntry[];
  tariffMode: () => boolean;
};

export function defaultPaymentMethod(methods: PaymentMethod[] | null | undefined): string {
  return String((methods || []).find((method) => !method.disabled)?.id || "");
}

export function createBillingModalActions({
  billingStore,
  devicesEnabled,
  devicesStore,
  methods,
  plans,
  suggestedPromoCode,
  singleTariffMode,
  subscription,
  tariffCatalog,
  tariffMode,
}: BillingModalActionDeps) {
  function currentDefaultPaymentMethod() {
    return defaultPaymentMethod(methods());
  }

  function openPaymentModal() {
    billingStore.openPaymentModal(
      tariffMode(),
      singleTariffMode(),
      tariffCatalog(),
      subscription(),
      plans(),
      currentDefaultPaymentMethod(),
      { suggestedPromoCode: suggestedPromoCode() }
    );
  }

  function openTopupModal(kind: string) {
    billingStore.openTopupModal(kind, currentDefaultPaymentMethod());
  }

  function openRegularTopupModal() {
    openTopupModal("regular");
  }

  function openPremiumTopupModal() {
    openTopupModal("premium");
  }

  function openTariffChangeModal() {
    billingStore.openTariffChangeModal(currentDefaultPaymentMethod());
  }

  function openDeviceTopupModal() {
    billingStore.openDeviceTopupModal(currentDefaultPaymentMethod());
  }

  function closeDeviceTopupModal() {
    billingStore.closeDeviceTopupModal();
  }

  function loadDevices(force = false) {
    return devicesStore.loadDevices(devicesEnabled(), force);
  }

  function disconnectDevice() {
    return devicesStore.disconnectDevice(devicesEnabled());
  }

  return {
    defaultPaymentMethod: currentDefaultPaymentMethod,
    openPaymentModal,
    openTopupModal,
    openRegularTopupModal,
    openPremiumTopupModal,
    openTariffChangeModal,
    openDeviceTopupModal,
    closeDeviceTopupModal,
    loadDevices,
    disconnectDevice,
  };
}

import { DATASET, defaultClone, type DemoRecord, type DemoTicket } from "./dataset";

let demoPromosState: DemoRecord[] | null = null;
let demoAdsState: DemoRecord[] | null = null;
let demoSupportTicketsState: DemoTicket[] | null = null;
let demoSupportMessagesState: Record<string, DemoRecord[]> | null = null;
let demoTariffsState: DemoRecord | null = null;
let demoBroadcastsState: DemoRecord[] | null = null;
let demoPaymentSequence = 20000;

export const demoSettingsChanges = new Map<string, { value?: unknown; deleted: boolean }>();

export type DemoPaymentStatus = {
  status: string;
  paid: boolean;
  sale_mode: string;
  device_count: number;
  applied: boolean;
};

export const demoPaymentStatuses = new Map<string, DemoPaymentStatus>();

const deviceTopupSaleModes = new Set(["hwid_device", "hwid_devices", "hwid_devices_renewal"]);

export function isDeviceTopupSaleMode(value: unknown): boolean {
  return deviceTopupSaleModes.has(String(value || "").toLowerCase());
}

export function nextDemoPaymentId(): number {
  return ++demoPaymentSequence;
}

export function demoPromos(): DemoRecord[] {
  if (!demoPromosState) demoPromosState = defaultClone(DATASET.promos || []);
  return demoPromosState;
}

export function setDemoPromos(next: DemoRecord[]): void {
  demoPromosState = next;
}

export function demoAds(): DemoRecord[] {
  if (!demoAdsState) demoAdsState = defaultClone(DATASET.ads || []);
  return demoAdsState;
}

export function setDemoAds(next: DemoRecord[]): void {
  demoAdsState = next;
}

export function demoSupportTickets(): DemoTicket[] {
  if (!demoSupportTicketsState) {
    demoSupportTicketsState = defaultClone(DATASET.supportTickets || []);
  }
  return demoSupportTicketsState;
}

export function demoSupportMessages(): Record<string, DemoRecord[]> {
  if (!demoSupportMessagesState) {
    demoSupportMessagesState = defaultClone(DATASET.supportMessages || {});
  }
  return demoSupportMessagesState;
}

export function demoTariffs(): DemoRecord {
  if (!demoTariffsState) {
    demoTariffsState = defaultClone(
      DATASET.tariffsCatalog || {
        default_tariff: "",
        topup_packages_default: { rub: [], stars: [] },
        tariffs: [],
      }
    );
  }
  return demoTariffsState;
}

export function setDemoTariffs(next: DemoRecord): void {
  demoTariffsState = next;
}

function initialDemoBroadcasts(): DemoRecord[] {
  const now = Date.now();
  const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();
  return [
    {
      broadcast_id: 103,
      status: "scheduled",
      target: "active",
      channels: ["telegram", "email"],
      texts: {
        ru: "Напоминаем о технических работах сегодня ночью. Доступ к сервису может кратковременно прерываться.",
        en: "Scheduled maintenance is planned for tonight. Service may be briefly interrupted.",
      },
      email_subjects: { ru: "Технические работы", en: "Scheduled maintenance" },
      buttons: [
        {
          kind: "url",
          label: "Статус сервиса",
          labels: {},
          url: "https://status.example.com",
          promo_code: "",
          section: "",
        },
      ],
      scheduled_at: iso(3_600_000),
      created_at: iso(-600_000),
      started_at: null,
      finished_at: null,
      updated_at: iso(-600_000),
      recipient_count: 0,
      total_deliveries: 0,
      successful_deliveries: 0,
      failed_deliveries: 0,
      telegram_sent: 0,
      telegram_failed: 0,
      email_sent: 0,
      email_failed: 0,
      last_error: null,
    },
    {
      broadcast_id: 102,
      status: "running",
      target: "all",
      channels: ["telegram", "email"],
      texts: {
        ru: "Летнее обновление уже доступно. Откройте приложение и посмотрите, что изменилось!",
      },
      email_subjects: { ru: "Летнее обновление" },
      buttons: [],
      scheduled_at: iso(-120_000),
      created_at: iso(-300_000),
      started_at: iso(-120_000),
      finished_at: null,
      updated_at: iso(-10_000),
      recipient_count: 1280,
      total_deliveries: 1766,
      successful_deliveries: 618,
      failed_deliveries: 3,
      telegram_sent: 472,
      telegram_failed: 2,
      email_sent: 146,
      email_failed: 1,
      last_error: null,
    },
    {
      broadcast_id: 101,
      status: "completed_with_errors",
      target: "expired",
      channels: ["telegram"],
      texts: {
        ru: "Ваша подписка закончилась. Продлите её в мини-приложении, чтобы снова подключиться.",
      },
      email_subjects: {},
      buttons: [
        {
          kind: "webapp_section",
          label: "",
          labels: {},
          url: "",
          promo_code: "",
          section: "plans",
        },
      ],
      scheduled_at: iso(-86_400_000),
      created_at: iso(-90_000_000),
      started_at: iso(-86_400_000),
      finished_at: iso(-86_340_000),
      updated_at: iso(-86_340_000),
      recipient_count: 311,
      total_deliveries: 311,
      successful_deliveries: 306,
      failed_deliveries: 5,
      telegram_sent: 306,
      telegram_failed: 5,
      email_sent: 0,
      email_failed: 0,
      last_error: null,
    },
  ];
}

export function demoBroadcasts(): DemoRecord[] {
  if (!demoBroadcastsState) demoBroadcastsState = initialDemoBroadcasts();
  return demoBroadcastsState;
}

export function setDemoBroadcasts(next: DemoRecord[]): void {
  demoBroadcastsState = next;
}

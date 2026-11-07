import type { ApiClient } from "./publicApi.js";
import { unwrap } from "./publicApi.js";

export type PushPermissionState = "default" | "denied" | "granted" | "unsupported";

export type WebPushDevice = {
  id: string;
  label: string;
  current: boolean;
  created_at?: string | null;
  last_seen_at?: string | null;
};

export type WebPushStatus = {
  configured: boolean;
  pwa_enabled: boolean;
  public_key: string;
  preference_enabled: boolean;
  devices: WebPushDevice[];
  current_device_id: string;
};

export type WebPushUiState =
  | "unsupported"
  | "insecure"
  | "embedded"
  | "denied"
  | "not_configured"
  | "pwa_disabled"
  | "ios_install"
  | "no_service_worker"
  | "default"
  | "disabled"
  | "enabled";

export type WebPushUiInput = {
  supported: boolean;
  secureContext: boolean;
  permission: PushPermissionState;
  configured: boolean;
  pwaEnabled: boolean;
  embedded: boolean;
  iosNotInstalled: boolean;
  serviceWorkerRegistered: boolean;
  preferenceEnabled: boolean;
  hasCurrentSubscription: boolean;
};

export type PushSubscriptionJson = {
  endpoint: string;
  expirationTime?: number | null;
  keys?: {
    p256dh?: string;
    auth?: string;
  };
};

const REVOKE_TIMEOUT_MS = 2000;
const READ_TIMEOUT_MS = 1500;

function navigatorValue(): Navigator | null {
  return typeof navigator === "undefined" ? null : navigator;
}

function windowValue(): Window | null {
  return typeof window === "undefined" ? null : window;
}

export function pushSupported(): boolean {
  const nav = navigatorValue();
  return Boolean(
    nav &&
    "serviceWorker" in nav &&
    typeof PushManager !== "undefined" &&
    typeof Notification !== "undefined"
  );
}

export function pushSecureContext(): boolean {
  try {
    return windowValue()?.isSecureContext !== false;
  } catch {
    return false;
  }
}

export function pushPermissionState(): PushPermissionState {
  if (!pushSupported()) return "unsupported";
  try {
    const permission = Notification.permission;
    if (permission === "denied" || permission === "granted") return permission;
    return "default";
  } catch {
    return "unsupported";
  }
}

export function base64UrlToUint8Array(value: string): Uint8Array<ArrayBuffer> {
  const normalized = String(value || "")
    .trim()
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4 || 4)) % 4);
  if (!padded) return new Uint8Array(new ArrayBuffer(0));
  const binary = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function withTimeout<T>(
  milliseconds: number,
  run: (signal: AbortSignal) => Promise<T>
): Promise<T | null> {
  if (typeof AbortController === "undefined") return Promise.resolve(null);
  const controller = new AbortController();
  return new Promise<T | null>((resolve) => {
    let settled = false;
    const finish = (value: T | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => {
      controller.abort();
      finish(null);
    }, milliseconds);
    run(controller.signal).then(
      (value) => finish(value),
      () => finish(null)
    );
  });
}

async function currentRegistration(): Promise<ServiceWorkerRegistration | null> {
  const nav = navigatorValue();
  if (!nav || !("serviceWorker" in nav)) return null;
  try {
    if (typeof nav.serviceWorker.getRegistration === "function") {
      const existing = await nav.serviceWorker.getRegistration("/");
      if (existing) return existing;
    }
    return await nav.serviceWorker.ready;
  } catch {
    return null;
  }
}

export async function getCurrentPushSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported() || !pushSecureContext()) return null;
  return withTimeout(READ_TIMEOUT_MS, async () => {
    const registration = await currentRegistration();
    if (!registration?.pushManager) return null;
    return registration.pushManager.getSubscription();
  });
}

export async function hasPushServiceWorkerRegistration(): Promise<boolean> {
  if (!pushSupported() || !pushSecureContext()) return false;
  const registration = await withTimeout(READ_TIMEOUT_MS, async () => currentRegistration());
  return Boolean(registration?.pushManager);
}

export async function fetchCurrentPushStatus(
  api: ApiClient["api"]
): Promise<{ subscription: PushSubscription | null; status: WebPushStatus }> {
  // Passive read: never POST the subscription here. Explicit enrollment is the
  // only path allowed to clear a server-side revocation.
  const subscription = await getCurrentPushSubscription();
  const status = await fetchWebPushStatus(api, subscription?.endpoint || "");
  return { subscription, status };
}

export async function syncCurrentPushSubscription(
  api: ApiClient["api"],
  label = "",
  options: { signal?: AbortSignal; isCurrent?: () => boolean } = {}
): Promise<WebPushStatus | null> {
  const subscription = await getCurrentPushSubscription();
  const endpoint = String(subscription?.endpoint || "").trim();
  if (!subscription || !endpoint || options.isCurrent?.() === false) return null;
  return syncPushSubscription(api, subscription, label, true, options.signal);
}

export type PushAccountSync = {
  syncForAccount(userId: unknown): Promise<WebPushStatus | null>;
  reset(): void;
};

export function createPushAccountSync(api: ApiClient["api"], label = ""): PushAccountSync {
  let confirmedUserId = "";
  let pendingUserId = "";
  let lastResult: WebPushStatus | null = null;
  let drain: Promise<void> | null = null;
  let generation = 0;
  let controller: AbortController | null = null;
  let ambiguousTarget = "";

  function startDrain(): Promise<void> {
    if (drain) return drain;
    drain = (async () => {
      while (pendingUserId && pendingUserId !== confirmedUserId) {
        const target = pendingUserId;
        const version = generation;
        controller = new AbortController();
        const result = await syncCurrentPushSubscription(api, label, {
          signal: controller.signal,
          isCurrent: () => version === generation,
        });
        if (version !== generation) continue;
        lastResult = result;
        // A null result is ambiguous: the request may have committed after the
        // response was lost. Clear confirmation so the latest pending identity
        // is retried and can reconcile the server back to the desired account.
        if (result) {
          confirmedUserId = target;
          ambiguousTarget = "";
        } else {
          confirmedUserId = "";
          if (pendingUserId === target && ambiguousTarget !== target) {
            ambiguousTarget = target;
            continue;
          }
        }
        if (pendingUserId === target) break;
      }
    })().finally(() => {
      drain = null;
    });
    return drain;
  }

  return {
    async syncForAccount(userId: unknown) {
      const id = String(userId ?? "").trim();
      pendingUserId = id;
      if (!id) {
        this.reset();
        return null;
      }
      if (id === confirmedUserId && !drain) return lastResult;
      await startDrain();
      return lastResult;
    },
    reset() {
      generation += 1;
      controller?.abort();
      confirmedUserId = "";
      pendingUserId = "";
      lastResult = null;
      ambiguousTarget = "";
    },
  };
}

export type PushDeviceRemovalDeps = {
  readSubscription(): Promise<PushSubscription | null>;
  revoke(deviceId: string): Promise<boolean>;
  refreshPassive(): Promise<{
    subscription: PushSubscription | null;
    status: WebPushStatus;
  } | null>;
};

export async function removePushDevice(
  deviceId: string,
  current: boolean,
  deps: PushDeviceRemovalDeps
): Promise<{ removed: boolean; status: WebPushStatus | null }> {
  if (current) {
    const subscription = await deps.readSubscription();
    try {
      await subscription?.unsubscribe();
    } catch {
      // Server-side revocation below is authoritative.
    }
  }
  const removed = await deps.revoke(deviceId);
  if (!removed) return { removed: false, status: null };
  const passive = await deps.refreshPassive();
  return { removed: true, status: passive?.status ?? null };
}

export type PushEnrollmentError =
  "" | "subscription_failed" | "not_confirmed" | "preference_failed" | "compensation_failed";

export type PushEnrollmentResult = {
  ok: boolean;
  status: WebPushStatus | null;
  error: PushEnrollmentError;
};

export type PushEnrollmentDeps = {
  previousPreference: boolean;
  subscribe(applicationServerKey: string): Promise<PushSubscription | null>;
  sync(subscription: PushSubscription): Promise<WebPushStatus | null>;
  savePreference(enabled: boolean): Promise<boolean>;
  confirm(): Promise<WebPushStatus | null>;
  revoke(subscription: PushSubscription): Promise<boolean>;
};

export function currentPushDeviceConfirmed(status: WebPushStatus | null): boolean {
  return Boolean(
    status?.current_device_id &&
    status.devices.some((device) => device.current && device.id === status.current_device_id)
  );
}

export async function enrollPushDevice(
  applicationServerKey: string,
  deps: PushEnrollmentDeps
): Promise<PushEnrollmentResult> {
  const subscription = await deps.subscribe(applicationServerKey);
  if (!subscription) {
    return { ok: false, status: null, error: "subscription_failed" };
  }
  let preferenceAttempted = false;
  let error: PushEnrollmentError = "not_confirmed";
  try {
    const status = await deps.sync(subscription);
    if (currentPushDeviceConfirmed(status)) {
      preferenceAttempted = true;
      error = "preference_failed";
      if (await deps.savePreference(true)) {
        error = "not_confirmed";
        const confirmed = await deps.confirm();
        if (currentPushDeviceConfirmed(confirmed) && confirmed?.preference_enabled) {
          return { ok: true, status: confirmed, error: "" };
        }
      }
    }
  } catch {
    // A failed response can still have committed; compensate by endpoint.
  }
  let revoked = false;
  let preferenceRestored = !preferenceAttempted;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (!revoked) {
      try {
        revoked = await deps.revoke(subscription);
      } catch {
        revoked = false;
      }
    }
    if (!preferenceRestored) {
      try {
        preferenceRestored = await deps.savePreference(deps.previousPreference);
      } catch {
        preferenceRestored = false;
      }
    }
    if (revoked && preferenceRestored) break;
  }
  if (!revoked) {
    // The server row may still be active; make this browser unable to receive
    // pushes even when the revoke response was lost.
    try {
      await subscription.unsubscribe();
    } catch {
      // The compensation_failed state below surfaces the incomplete cleanup.
    }
  }
  // Never expose a partially enrolled device as active, including stale status.
  return {
    ok: false,
    status: null,
    error: revoked && preferenceRestored ? error : "compensation_failed",
  };
}

export async function revokePushSubscription(
  api: ApiClient["api"],
  subscription: PushSubscription
): Promise<boolean> {
  const response = await api("/plugins/web_push/subscription", {
    method: "DELETE",
    body: JSON.stringify({ endpoint: subscription.endpoint }),
  });
  return Boolean(response?.ok);
}

export async function revokeCurrentPushSubscription(api: ApiClient["api"]): Promise<void> {
  try {
    const subscription = await getCurrentPushSubscription();
    const endpoint = String(subscription?.endpoint || "").trim();
    if (!endpoint) return;
    await withTimeout(REVOKE_TIMEOUT_MS, async (signal) => {
      const response = await api("/plugins/web_push/subscription", {
        method: "DELETE",
        body: JSON.stringify({ endpoint }),
        signal,
      });
      if (!response?.ok) throw response;
    });
  } catch {
    // Logout and account switching must never be blocked by push cleanup.
  }
}

export async function fetchWebPushStatus(
  api: ApiClient["api"],
  endpoint = ""
): Promise<WebPushStatus> {
  const response = await api("/plugins/web_push/status", {
    method: "POST",
    body: JSON.stringify(endpoint ? { endpoint } : {}),
  });
  const payload = unwrap(response) as unknown as WebPushStatus & { ok: boolean };
  return {
    configured: payload.configured === true,
    pwa_enabled: payload.pwa_enabled === true,
    public_key: typeof payload.public_key === "string" ? payload.public_key : "",
    preference_enabled: payload.preference_enabled !== false,
    devices: Array.isArray(payload.devices) ? payload.devices : [],
    current_device_id:
      typeof payload.current_device_id === "string" ? payload.current_device_id : "",
  };
}

export async function syncPushSubscription(
  api: ApiClient["api"],
  subscription: PushSubscription,
  label = "",
  passive = false,
  signal?: AbortSignal
): Promise<WebPushStatus | null> {
  try {
    const payload = subscription.toJSON() as PushSubscriptionJson;
    const options = {
      method: "POST",
      body: JSON.stringify({
        subscription: payload,
        label: String(label || "").slice(0, 120),
      }),
      ...(signal ? { signal } : {}),
    };
    const response = passive
      ? await api("/plugins/web_push/subscription/sync", options)
      : await api("/plugins/web_push/subscription", options);
    const result = unwrap(response) as unknown as WebPushStatus & { ok: boolean };
    return {
      configured: result.configured === true,
      pwa_enabled: result.pwa_enabled === true,
      public_key: typeof result.public_key === "string" ? result.public_key : "",
      preference_enabled: result.preference_enabled !== false,
      devices: Array.isArray(result.devices) ? result.devices : [],
      current_device_id:
        typeof result.current_device_id === "string" ? result.current_device_id : "",
    };
  } catch {
    return null;
  }
}

export async function setPushPreference(api: ApiClient["api"], enabled: boolean): Promise<boolean> {
  try {
    const response = await api("/account/notification-preferences", {
      method: "POST",
      body: JSON.stringify({ push_notifications_enabled: enabled }),
    });
    return Boolean(response?.ok);
  } catch {
    return false;
  }
}

function byteView(value: unknown): Uint8Array<ArrayBuffer> | null {
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (ArrayBuffer.isView(value)) {
    const bytes = new Uint8Array(value.byteLength);
    bytes.set(new Uint8Array(value.buffer, value.byteOffset, value.byteLength));
    return bytes;
  }
  return null;
}

export function pushSubscriptionUsesKey(
  subscription: Pick<PushSubscription, "options">,
  applicationServerKey: string
): boolean {
  const existing = byteView(subscription.options?.applicationServerKey);
  const expected = base64UrlToUint8Array(applicationServerKey);
  if (!existing || existing.length !== expected.length) return false;
  return existing.every((value, index) => value === expected[index]);
}

export async function subscribeCurrentDevice(
  applicationServerKey: string
): Promise<PushSubscription | null> {
  if (!pushSupported() || !pushSecureContext()) return null;
  try {
    if (pushPermissionState() === "default") {
      const granted = await Notification.requestPermission();
      if (granted !== "granted") return null;
    }
    if (pushPermissionState() !== "granted") return null;
    const registration = await withTimeout(5000, async () => currentRegistration());
    if (!registration?.pushManager) return null;
    const existing = await registration.pushManager.getSubscription();
    if (existing) {
      if (pushSubscriptionUsesKey(existing, applicationServerKey)) return existing;
      // VAPID rotation: a subscription created with the old application
      // server key can never be delivered by the new server key.
      try {
        await existing.unsubscribe();
      } catch {
        return null;
      }
    }
    return await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64UrlToUint8Array(applicationServerKey),
    });
  } catch {
    return null;
  }
}

export async function revokePushDevice(api: ApiClient["api"], deviceId: string): Promise<boolean> {
  try {
    const response = await api("/plugins/web_push/subscription", {
      method: "DELETE",
      body: JSON.stringify({ device_id: String(deviceId || "").trim() }),
    });
    return Boolean(response?.ok);
  } catch {
    return false;
  }
}

export function resolveWebPushUiState(input: WebPushUiInput): WebPushUiState {
  // Known prerequisites first: a missing Push API must not hide an actionable
  // HTTPS/Telegram/iOS/PWA explanation.
  if (!input.secureContext) return "insecure";
  if (input.embedded) return "embedded";
  if (input.iosNotInstalled) return "ios_install";
  if (!input.pwaEnabled) return "pwa_disabled";
  if (!input.supported) return "unsupported";
  if (!input.serviceWorkerRegistered) return "no_service_worker";
  if (input.permission === "denied") return "denied";
  if (!input.configured) return "not_configured";
  if (input.permission === "default") return "default";
  if (input.preferenceEnabled && input.hasCurrentSubscription) return "enabled";
  if (!input.preferenceEnabled) return "disabled";
  return "default";
}

export function currentDeviceLabel(device: WebPushDevice): string {
  return String(device.label || "").trim() || device.id.slice(0, 8);
}

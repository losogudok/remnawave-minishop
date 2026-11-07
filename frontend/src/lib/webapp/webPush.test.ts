import { afterEach, describe, expect, it, vi } from "vitest";

import {
  base64UrlToUint8Array,
  createPushAccountSync,
  enrollPushDevice,
  fetchCurrentPushStatus,
  getCurrentPushSubscription,
  hasPushServiceWorkerRegistration,
  pushPermissionState,
  pushSubscriptionUsesKey,
  pushSupported,
  removePushDevice,
  resolveWebPushUiState,
  revokeCurrentPushSubscription,
  revokePushSubscription,
  setPushPreference,
  syncPushSubscription,
  subscribeCurrentDevice,
  type WebPushStatus,
  type WebPushUiInput,
} from "./webPush.js";

const baseInput: WebPushUiInput = {
  supported: true,
  secureContext: true,
  permission: "granted",
  configured: true,
  pwaEnabled: true,
  embedded: false,
  iosNotInstalled: false,
  serviceWorkerRegistered: true,
  preferenceEnabled: true,
  hasCurrentSubscription: true,
};

const statusPayload: WebPushStatus = {
  configured: true,
  pwa_enabled: true,
  public_key: "public-key",
  preference_enabled: true,
  devices: [
    {
      id: "device-one",
      label: "Pixel",
      current: true,
      created_at: null,
      last_seen_at: null,
    },
  ],
  current_device_id: "device-one",
};

function makeSubscription(applicationServerKey?: Uint8Array) {
  return {
    endpoint: "https://fcm.googleapis.com/fcm/send/abc",
    options: {
      applicationServerKey: applicationServerKey
        ? new Uint8Array(applicationServerKey).buffer
        : undefined,
    },
    toJSON: () => ({
      endpoint: "https://fcm.googleapis.com/fcm/send/abc",
      keys: { p256dh: "p", auth: "a" },
    }),
    unsubscribe: vi.fn(async () => true),
  };
}

function stubBrowser({
  permission = "granted",
  subscribe = false,
  applicationServerKey,
}: {
  permission?: NotificationPermission;
  subscribe?: boolean;
  applicationServerKey?: Uint8Array;
} = {}) {
  const subscription = makeSubscription(applicationServerKey);
  const registration = {
    pushManager: {
      getSubscription: vi.fn(async () => (subscribe ? subscription : null)),
      subscribe: vi.fn(async () => subscription),
    },
  };
  const requestPermission = vi.fn(async () => permission);
  vi.stubGlobal("navigator", {
    userAgent: "Test Browser",
    isSecureContext: true,
    serviceWorker: {
      getRegistration: vi.fn(async () => registration),
      ready: Promise.resolve(registration),
    },
  });
  vi.stubGlobal("window", { isSecureContext: true });
  vi.stubGlobal("Notification", { permission, requestPermission });
  vi.stubGlobal("PushManager", class {});
  return { subscription, requestPermission };
}

function stubNoRegistration() {
  vi.stubGlobal("navigator", {
    userAgent: "Test Browser",
    isSecureContext: true,
    serviceWorker: {
      getRegistration: vi.fn(async () => undefined),
      ready: new Promise(() => {}),
    },
  });
  vi.stubGlobal("window", { isSecureContext: true });
  vi.stubGlobal("Notification", { permission: "granted", requestPermission: vi.fn() });
  vi.stubGlobal("PushManager", class {});
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("webPush helpers", () => {
  it("decodes unpadded base64url application server keys", () => {
    expect(Array.from(base64UrlToUint8Array("AQID"))).toEqual([1, 2, 3]);
    expect(Array.from(base64UrlToUint8Array("-_8"))).toEqual([251, 255]);
    expect(base64UrlToUint8Array("").length).toBe(0);
  });

  it("reports unsupported browsers and permission states", () => {
    expect(pushSupported()).toBe(false);
    expect(pushPermissionState()).toBe("unsupported");
    stubBrowser({ permission: "denied" });
    expect(pushSupported()).toBe(true);
    expect(pushPermissionState()).toBe("denied");
    stubBrowser({ permission: "default" });
    expect(pushPermissionState()).toBe("default");
  });

  it("returns the current browser subscription", async () => {
    stubBrowser({ subscribe: false });
    expect(await getCurrentPushSubscription()).toBeNull();
    const { subscription } = stubBrowser({ subscribe: true });
    expect(await getCurrentPushSubscription()).toBe(subscription);
    expect(subscription.endpoint).toContain("fcm.googleapis.com");
  });

  it("revokes only the current endpoint and never throws", async () => {
    stubBrowser({ subscribe: false });
    const api = vi.fn(async () => ({ ok: true }));
    await revokeCurrentPushSubscription(api as never);
    expect(api).not.toHaveBeenCalled();

    const { subscription } = stubBrowser({ subscribe: true });
    await revokeCurrentPushSubscription(api as never);
    expect(api).toHaveBeenCalledWith("/plugins/web_push/subscription", {
      method: "DELETE",
      body: JSON.stringify({ endpoint: subscription.endpoint }),
      signal: expect.any(AbortSignal),
    });

    const failing = vi.fn(async () => {
      throw new Error("offline");
    });
    await expect(revokeCurrentPushSubscription(failing as never)).resolves.toBeUndefined();
  });

  it("reads status passively without re-posting the subscription", async () => {
    stubBrowser({ subscribe: true });
    const api = vi.fn(async () => ({ ok: true, ...statusPayload }));
    const result = await fetchCurrentPushStatus(api as never);
    expect(result.status.current_device_id).toBe("device-one");
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/plugins/web_push/status", {
      method: "POST",
      body: JSON.stringify({
        endpoint: "https://fcm.googleapis.com/fcm/send/abc",
      }),
    });
    expect(api).not.toHaveBeenCalledWith("/plugins/web_push/subscription", expect.anything());
  });

  it("rebinds an existing subscription once per authenticated account transition", async () => {
    const { requestPermission } = stubBrowser({ subscribe: true });
    const api = vi.fn(async () => ({ ok: true, ...statusPayload }));
    const sync = createPushAccountSync(api as never);

    await sync.syncForAccount(1);
    await sync.syncForAccount(1);
    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith("/plugins/web_push/subscription/sync", {
      method: "POST",
      body: expect.stringContaining('"endpoint":"https://fcm.googleapis.com/fcm/send/abc"'),
      signal: expect.any(AbortSignal),
    });
    await sync.syncForAccount(2);
    expect(api).toHaveBeenCalledTimes(2);
    sync.reset();
    await sync.syncForAccount(1);
    expect(api).toHaveBeenCalledTimes(3);
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("invalidates obsolete browser reads on logout", async () => {
    const { subscription } = stubBrowser({ subscribe: true });
    let finishRead!: (value: PushSubscription) => void;
    const registration = {
      pushManager: {
        getSubscription: vi.fn(
          () =>
            new Promise<PushSubscription>((resolve) => {
              finishRead = resolve;
            })
        ),
      },
    };
    vi.stubGlobal("navigator", {
      serviceWorker: { getRegistration: vi.fn(async () => registration) },
    });
    const api = vi.fn(async () => ({ ok: true, ...statusPayload }));
    const sync = createPushAccountSync(api as never);
    const pending = sync.syncForAccount("A");
    await vi.waitFor(() => expect(finishRead).toBeTypeOf("function"));
    sync.reset();
    finishRead(subscription as never);
    await pending;
    expect(api).not.toHaveBeenCalled();
  });

  it("ignores an obsolete response after reset and drains the new identity", async () => {
    stubBrowser({ subscribe: true });
    let finishA!: () => void;
    const api = vi.fn(async () => ({ ok: true, ...statusPayload }));
    api.mockImplementationOnce(async () => {
      await new Promise<void>((resolve) => {
        finishA = resolve;
      });
      return { ok: true, ...statusPayload };
    });
    const sync = createPushAccountSync(api as never);
    const a = sync.syncForAccount("A");
    await vi.waitFor(() => expect(finishA).toBeTypeOf("function"));
    const signal = (api.mock.calls as unknown as [string, RequestInit][])[0][1].signal;
    sync.reset();
    expect(signal?.aborted).toBe(true);
    const b = sync.syncForAccount("B");
    finishA();
    await Promise.all([a, b]);
    expect(api).toHaveBeenCalledTimes(2);
    await sync.syncForAccount("B");
    expect(api).toHaveBeenCalledTimes(2);
  });

  it("keeps a removed device revoked when browser unsubscribe throws", async () => {
    const subscription = makeSubscription();
    subscription.unsubscribe.mockRejectedValueOnce(new Error("blocked"));
    const refreshPassive = vi.fn(async () => ({
      subscription: null,
      status: { ...statusPayload, devices: [], current_device_id: "" },
    }));
    const revoke = vi.fn(async () => true);
    const result = await removePushDevice("device-one", true, {
      readSubscription: vi.fn(async () => subscription as never),
      revoke,
      refreshPassive,
    });
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith("device-one");
    expect(refreshPassive).toHaveBeenCalledOnce();
    expect(result).toMatchObject({
      removed: true,
      status: { current_device_id: "", devices: [] },
    });
  });

  it("converges A -> pending B -> A back to A", async () => {
    stubBrowser({ subscribe: true });
    let finishB!: () => void;
    const api = vi.fn(async () => ({ ok: true, ...statusPayload }));
    const sync = createPushAccountSync(api as never);
    await sync.syncForAccount("A");
    api.mockImplementationOnce(async () => {
      await new Promise<void>((resolve) => {
        finishB = resolve;
      });
      return { ok: true, ...statusPayload };
    });
    const b = sync.syncForAccount("B");
    await vi.waitFor(() => expect(finishB).toBeTypeOf("function"));
    const a = sync.syncForAccount("A");
    finishB();
    await Promise.all([a, b]);
    expect(api).toHaveBeenCalledTimes(3);
  });

  it("reconciles A -> B -> A when B's response is lost", async () => {
    stubBrowser({ subscribe: true });
    let finishB!: () => void;
    let serverOwner = "";
    let call = 0;
    const api = vi.fn(async () => {
      call += 1;
      if (call === 1) {
        serverOwner = "A";
        return { ok: true, ...statusPayload };
      }
      if (call === 2) {
        serverOwner = "B";
        await new Promise<void>((resolve) => {
          finishB = resolve;
        });
        return null;
      }
      serverOwner = "A";
      return { ok: true, ...statusPayload };
    });
    const sync = createPushAccountSync(api as never);
    await sync.syncForAccount("A");
    const b = sync.syncForAccount("B");
    await vi.waitFor(() => expect(finishB).toBeTypeOf("function"));
    const a = sync.syncForAccount("A");
    finishB();
    await Promise.all([a, b]);
    expect(serverOwner).toBe("A");
    expect(api).toHaveBeenCalledTimes(3);
  });

  it.each(["post", "preference", "confirmation"] as const)(
    "revokes only the enrolled endpoint and restores preference on %s failure",
    async (failure) => {
      const subscription = makeSubscription();
      const savePreference = vi.fn(
        async (enabled: boolean) => failure !== "preference" || !enabled
      );
      const revoke = vi.fn(async () => true);
      const confirm = vi.fn(async () =>
        failure === "confirmation" ? { ...statusPayload, current_device_id: "" } : statusPayload
      );
      const result = await enrollPushDevice("AQID", {
        previousPreference: false,
        subscribe: vi.fn(async () => subscription as never),
        sync: vi.fn(async () => (failure === "post" ? null : statusPayload)),
        savePreference,
        revoke,
        confirm,
      });
      expect(result.ok).toBe(false);
      expect(result.status?.current_device_id).toBeFalsy();
      expect(revoke).toHaveBeenCalledExactlyOnceWith(subscription);
      if (failure === "post") {
        expect(savePreference).not.toHaveBeenCalled();
      } else {
        expect(savePreference.mock.calls).toEqual([[true], [false]]);
      }
    }
  );

  it("compensates an accepted POST when preference save fails through the API", async () => {
    const subscription = makeSubscription();
    const devices = new Set(["other-device"]);
    let preference = false;
    const api = vi.fn(async (path: string, options: RequestInit) => {
      if (path === "/plugins/web_push/subscription") {
        const payload = JSON.parse(String(options.body));
        if (options.method === "POST") devices.add(subscription.endpoint);
        else devices.delete(payload.endpoint);
        return { ok: true, ...statusPayload };
      }
      const enabled = JSON.parse(String(options.body)).push_notifications_enabled;
      // Simulate a committed write with an unsuccessful response.
      preference = enabled;
      return { ok: !enabled };
    });
    const result = await enrollPushDevice("AQID", {
      previousPreference: false,
      subscribe: async () => subscription as never,
      sync: (current) => syncPushSubscription(api as never, current),
      savePreference: (enabled) => setPushPreference(api as never, enabled),
      revoke: (current) => revokePushSubscription(api as never, current),
      confirm: vi.fn(async () => statusPayload),
    });
    expect(result.ok).toBe(false);
    expect(devices).toEqual(new Set(["other-device"]));
    expect(preference).toBe(false);
  });

  it("restores a previously enabled preference and leaves other devices alone", async () => {
    const subscription = makeSubscription();
    const savePreference = vi.fn(async () => false);
    const revoke = vi.fn(async () => true);
    const result = await enrollPushDevice("AQID", {
      previousPreference: true,
      subscribe: vi.fn(async () => subscription as never),
      sync: vi.fn(async () => statusPayload),
      savePreference,
      revoke,
      confirm: vi.fn(async () => statusPayload),
    });
    expect(result.ok).toBe(false);
    expect(result.error).toBe("compensation_failed");
    expect(savePreference.mock.calls).toEqual([[true], [true], [true]]);
    expect(revoke).toHaveBeenCalledExactlyOnceWith(subscription);
  });

  it("reports compensation_failed and unsubscribes when DELETE cannot be confirmed", async () => {
    const subscription = makeSubscription();
    const savePreference = vi.fn(async (enabled: boolean) => !enabled);
    const revoke = vi.fn(async () => false);
    const result = await enrollPushDevice("AQID", {
      previousPreference: false,
      subscribe: vi.fn(async () => subscription as never),
      sync: vi.fn(async () => statusPayload),
      savePreference,
      revoke,
      confirm: vi.fn(async () => ({ ...statusPayload, current_device_id: "" })),
    });
    expect(result.ok).toBe(false);
    expect(result.error).toBe("compensation_failed");
    expect(revoke).toHaveBeenCalledTimes(2);
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
  });

  it("retries compensation once and reports the original error when cleanup succeeds", async () => {
    const subscription = makeSubscription();
    const savePreference = vi
      .fn()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    const revoke = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const result = await enrollPushDevice("AQID", {
      previousPreference: false,
      subscribe: vi.fn(async () => subscription as never),
      sync: vi.fn(async () => statusPayload),
      savePreference,
      revoke,
      confirm: vi.fn(async () => ({ ...statusPayload, current_device_id: "" })),
    });
    expect(result.ok).toBe(false);
    expect(result.error).toBe("not_confirmed");
    expect(revoke).toHaveBeenCalledTimes(2);
    expect(savePreference).toHaveBeenCalledTimes(3);
    expect(subscription.unsubscribe).not.toHaveBeenCalled();
  });

  it("stays compensation_failed after two incomplete cleanup attempts", async () => {
    const subscription = makeSubscription();
    const savePreference = vi
      .fn()
      .mockResolvedValueOnce(true)
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(false);
    const revoke = vi.fn(async () => false);
    const result = await enrollPushDevice("AQID", {
      previousPreference: false,
      subscribe: vi.fn(async () => subscription as never),
      sync: vi.fn(async () => statusPayload),
      savePreference,
      revoke,
      confirm: vi.fn(async () => ({ ...statusPayload, current_device_id: "" })),
    });
    expect(result).toEqual({ ok: false, status: null, error: "compensation_failed" });
    expect(revoke).toHaveBeenCalledTimes(2);
    expect(savePreference).toHaveBeenCalledTimes(3);
    expect(subscription.unsubscribe).toHaveBeenCalledOnce();
  });

  it("activates only after enrollment, preference save and final confirmation", async () => {
    const subscription = makeSubscription();
    const revoke = vi.fn(async () => true);
    const confirm = vi.fn(async () => statusPayload);
    const result = await enrollPushDevice("AQID", {
      previousPreference: false,
      subscribe: vi.fn(async () => subscription as never),
      sync: vi.fn(async () => statusPayload),
      savePreference: vi.fn(async () => true),
      revoke,
      confirm,
    });
    expect(result.ok).toBe(true);
    expect(confirm).toHaveBeenCalledOnce();
    expect(revoke).not.toHaveBeenCalled();
  });

  it("re-subscribes when the VAPID application server key rotates", async () => {
    const oldKey = base64UrlToUint8Array("AQID");
    const newKey = base64UrlToUint8Array("BAUG");
    const oldSubscription = makeSubscription(oldKey);
    const newSubscription = makeSubscription(newKey);
    const registration = {
      pushManager: {
        getSubscription: vi.fn(async () => oldSubscription),
        subscribe: vi.fn(async () => newSubscription),
      },
    };
    vi.stubGlobal("navigator", {
      userAgent: "Test Browser",
      isSecureContext: true,
      serviceWorker: {
        getRegistration: vi.fn(async () => registration),
        ready: Promise.resolve(registration),
      },
    });
    vi.stubGlobal("window", { isSecureContext: true });
    vi.stubGlobal("Notification", {
      permission: "granted",
      requestPermission: vi.fn(async () => "granted"),
    });
    vi.stubGlobal("PushManager", class {});

    expect(pushSubscriptionUsesKey(oldSubscription as never, "AQID")).toBe(true);
    expect(pushSubscriptionUsesKey(oldSubscription as never, "BAUG")).toBe(false);
    expect(await subscribeCurrentDevice("BAUG")).toBe(newSubscription);
    expect(oldSubscription.unsubscribe).toHaveBeenCalledOnce();
    expect(registration.pushManager.subscribe).toHaveBeenCalledWith({
      userVisibleOnly: true,
      applicationServerKey: expect.any(Uint8Array),
    });
  });

  it("times out a missing service worker registration instead of hanging", async () => {
    vi.useFakeTimers();
    stubNoRegistration();
    const pending = getCurrentPushSubscription();
    await vi.advanceTimersByTimeAsync(1600);
    await expect(pending).resolves.toBeNull();
    const registered = hasPushServiceWorkerRegistration();
    await vi.advanceTimersByTimeAsync(1600);
    await expect(registered).resolves.toBe(false);
  });

  it("prioritizes platform prerequisites over generic unsupported capability", () => {
    expect(resolveWebPushUiState({ ...baseInput, secureContext: false, supported: false })).toBe(
      "insecure"
    );
    expect(resolveWebPushUiState({ ...baseInput, embedded: true, supported: false })).toBe(
      "embedded"
    );
    expect(resolveWebPushUiState({ ...baseInput, iosNotInstalled: true, supported: false })).toBe(
      "ios_install"
    );
    expect(resolveWebPushUiState({ ...baseInput, pwaEnabled: false, supported: false })).toBe(
      "pwa_disabled"
    );
    expect(resolveWebPushUiState({ ...baseInput, serviceWorkerRegistered: false })).toBe(
      "no_service_worker"
    );
    expect(resolveWebPushUiState({ ...baseInput, supported: false })).toBe("unsupported");
  });

  it("resolves guidance states before offering the enable action", () => {
    expect(resolveWebPushUiState({ ...baseInput, supported: false })).toBe("unsupported");
    expect(resolveWebPushUiState({ ...baseInput, secureContext: false })).toBe("insecure");
    expect(resolveWebPushUiState({ ...baseInput, embedded: true })).toBe("embedded");
    expect(resolveWebPushUiState({ ...baseInput, permission: "denied" })).toBe("denied");
    expect(resolveWebPushUiState({ ...baseInput, configured: false })).toBe("not_configured");
    expect(resolveWebPushUiState({ ...baseInput, pwaEnabled: false })).toBe("pwa_disabled");
    expect(resolveWebPushUiState({ ...baseInput, iosNotInstalled: true })).toBe("ios_install");
    expect(resolveWebPushUiState({ ...baseInput, permission: "default" })).toBe("default");
    expect(
      resolveWebPushUiState({
        ...baseInput,
        preferenceEnabled: false,
        hasCurrentSubscription: false,
      })
    ).toBe("disabled");
    expect(resolveWebPushUiState({ ...baseInput, hasCurrentSubscription: false })).toBe("default");
    expect(resolveWebPushUiState(baseInput)).toBe("enabled");
  });
});

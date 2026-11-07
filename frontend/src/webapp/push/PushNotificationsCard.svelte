<script lang="ts">
  import { onMount } from "svelte";
  import { Megaphone } from "$components/ui/icons.js";
  import Badge from "$components/ui/badge.svelte";
  import Button from "$components/ui/button.svelte";
  import Card from "$components/ui/card.svelte";
  import {
    currentDeviceLabel,
    currentPushDeviceConfirmed,
    enrollPushDevice,
    fetchCurrentPushStatus,
    fetchWebPushStatus,
    getCurrentPushSubscription,
    hasPushServiceWorkerRegistration,
    pushPermissionState,
    pushSecureContext,
    pushSupported,
    removePushDevice,
    resolveWebPushUiState,
    revokePushDevice,
    revokePushSubscription,
    setPushPreference,
    subscribeCurrentDevice,
    syncPushSubscription,
    type PushPermissionState,
    type WebPushStatus,
    type WebPushUiState,
  } from "$lib/webapp/webPush.js";
  import { getPwaInstallSnapshot } from "$lib/webapp/pwaInstall.svelte.js";
  import type { NotificationPreferences } from "$lib/webapp/notificationPreferences.js";
  import type { ApiClient } from "$lib/webapp/publicApi.js";
  import type { Translate } from "$lib/webapp/types.js";

  type Props = {
    api?: ApiClient["api"];
    preferences: NotificationPreferences;
    disabled?: boolean;
    t?: Translate;
    onPreferencesChange?: (
      preferences: NotificationPreferences
    ) => Promise<boolean | void> | boolean | void;
    initialStatus?: WebPushStatus | null;
    initialState?: WebPushUiState;
  };

  let {
    api = undefined,
    preferences,
    disabled = false,
    t = (key) => key,
    onPreferencesChange = undefined,
    initialStatus = null,
    initialState = undefined,
  }: Props = $props();

  let loadedStatus = $state<WebPushStatus | null>(null);
  let loadedUiState = $state<WebPushUiState | null>(null);
  const status = $derived(loadedStatus ?? initialStatus);
  const uiState = $derived.by(() => {
    const state = loadedUiState ?? initialState ?? "unsupported";
    if (state === "enabled") {
      if (!preferences.push_notifications_enabled || !status?.preference_enabled) return "disabled";
      if (!currentPushDeviceConfirmed(status)) return "default";
    }
    return state;
  });
  let permission = $state<PushPermissionState>("unsupported");
  let serviceWorkerRegistered = $state(true);
  let busy = $state(false);
  let message = $state("");
  let messageIsError = $state(false);
  let initialized = $state(false);

  function environment() {
    const snapshot = getPwaInstallSnapshot();
    return {
      supported: pushSupported(),
      secureContext: pushSecureContext(),
      permission: pushPermissionState(),
      pwaEnabled: snapshot.state !== "pwa-disabled",
      embedded: snapshot.state === "embedded",
      iosNotInstalled: snapshot.platform === "ios" && snapshot.state !== "installed",
    };
  }

  function applyUiState(overrides: Partial<Parameters<typeof resolveWebPushUiState>[0]> = {}) {
    const env = environment();
    permission = env.permission;
    const configured = status?.configured === true;
    loadedUiState = resolveWebPushUiState({
      supported: env.supported,
      secureContext: env.secureContext,
      permission: env.permission,
      configured,
      pwaEnabled: env.pwaEnabled,
      embedded: env.embedded,
      iosNotInstalled: env.iosNotInstalled,
      serviceWorkerRegistered,
      preferenceEnabled:
        preferences.push_notifications_enabled && status?.preference_enabled === true,
      hasCurrentSubscription: currentPushDeviceConfirmed(status),
      ...overrides,
    });
  }

  async function refresh(): Promise<void> {
    try {
      const env = environment();
      permission = env.permission;
      if (!env.supported || !env.secureContext) {
        serviceWorkerRegistered = true;
      } else {
        serviceWorkerRegistered = await hasPushServiceWorkerRegistration();
      }
      if (api) {
        // Passive status only: never re-POST an existing subscription here.
        // Only the explicit enable action may clear a server-side revocation.
        const passive = await fetchCurrentPushStatus(api);
        loadedStatus = passive.status;
      }
      applyUiState();
      initialized = true;
    } catch {
      // The card is advisory: a network/API failure must not break the page.
      applyUiState();
    }
  }

  onMount(() => {
    void refresh();
  });

  function enrollmentLabel(): string {
    return typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 120) : "";
  }

  async function enable(): Promise<void> {
    if (busy || disabled) return;
    busy = true;
    message = "";
    messageIsError = false;
    try {
      if (!status) {
        loadedStatus = api ? await fetchWebPushStatus(api) : null;
      }
      if (!api || !status?.configured || !status.public_key) {
        applyUiState();
        return;
      }
      const result = await enrollPushDevice(status.public_key, {
        previousPreference: preferences.push_notifications_enabled,
        subscribe: (key) => subscribeCurrentDevice(key),
        sync: (subscription) => syncPushSubscription(api, subscription, enrollmentLabel()),
        savePreference: (enabled) => savePreference(enabled),
        confirm: async () => (await fetchCurrentPushStatus(api)).status,
        revoke: (subscription) => revokePushSubscription(api, subscription),
      });
      if (result.status) loadedStatus = result.status;
      if (!result.ok) {
        if (status)
          loadedStatus = {
            ...status,
            current_device_id: "",
            devices: status.devices.filter((device) => !device.current),
          };
        applyUiState();
        message =
          result.error === "compensation_failed"
            ? t(
                "wa_push_compensation_failed",
                {},
                "Notifications were not enabled and cleanup could not be fully confirmed. Remove this device from the list or try again."
              )
            : t(
                "wa_push_enable_failed",
                {},
                "Could not enable notifications. Check browser permission and try again."
              );
        messageIsError = true;
        return;
      }
      serviceWorkerRegistered = true;
      applyUiState();
    } catch {
      message = t("wa_push_enable_failed", {}, "Could not enable notifications.");
      messageIsError = true;
    } finally {
      busy = false;
    }
  }

  async function disableAll(): Promise<void> {
    if (busy || disabled) return;
    busy = true;
    message = "";
    messageIsError = false;
    try {
      const saved = await savePreference(false);
      if (!saved) {
        message = t("wa_push_save_failed", {}, "Could not save the notification preference.");
        messageIsError = true;
      }
      applyUiState();
    } catch {
      message = t("wa_push_save_failed", {}, "Could not save the notification preference.");
      messageIsError = true;
    } finally {
      busy = false;
    }
  }

  async function savePreference(enabled: boolean): Promise<boolean> {
    if (onPreferencesChange) {
      const result = await onPreferencesChange({
        ...preferences,
        push_notifications_enabled: enabled,
      });
      return result !== false;
    }
    if (!api) return false;
    return setPushPreference(api, enabled);
  }

  async function removeDevice(deviceId: string, current: boolean): Promise<void> {
    if (busy || disabled || !api) return;
    busy = true;
    message = "";
    messageIsError = false;
    try {
      const result = await removePushDevice(deviceId, current, {
        readSubscription: () => getCurrentPushSubscription(),
        revoke: (id) => revokePushDevice(api, id),
        refreshPassive: () => fetchCurrentPushStatus(api),
      });
      if (!result.removed) {
        message = t("wa_push_disable_failed", {}, "Could not disable this device.");
        messageIsError = true;
        return;
      }
      if (result.status) loadedStatus = result.status;
      message = t("wa_push_device_removed", {}, "Device disabled.");
      messageIsError = false;
      applyUiState();
    } finally {
      busy = false;
    }
  }

  const guidance = $derived.by(() => {
    switch (uiState) {
      case "unsupported":
        return t(
          "wa_push_unsupported",
          {},
          "This browser does not support Web Push notifications."
        );
      case "insecure":
        return t(
          "wa_push_insecure",
          {},
          "Notifications require HTTPS. Open Minishop over a secure connection."
        );
      case "embedded":
        return t(
          "wa_push_embedded",
          {},
          "You opened Minishop in Telegram or an embedded browser. Open it in a regular browser to enable notifications."
        );
      case "denied":
        return t(
          "wa_push_denied",
          {},
          "Notifications are blocked for this site. Allow them in the browser's site settings, then reload this page."
        );
      case "not_configured":
        return t(
          "wa_push_not_configured",
          {},
          "Browser notifications are not available on this server yet."
        );
      case "pwa_disabled":
        return t(
          "wa_push_pwa_disabled",
          {},
          "Browser notifications need the installed Web App service worker. The administrator has disabled PWA support."
        );
      case "no_service_worker":
        return t(
          "wa_push_no_service_worker",
          {},
          "The notification service worker is not registered in this browser. Reload the page or reinstall the app, then try again."
        );
      case "ios_install":
        return t(
          "wa_push_ios_install",
          {},
          "On iPhone and iPad, add Minishop to the Home Screen first. Installing the app is required before iOS allows web notifications."
        );
      default:
        return "";
    }
  });

  const canEnable = $derived(
    !disabled &&
      !busy &&
      (uiState === "default" || uiState === "disabled") &&
      status?.configured === true
  );
  const canDisableAccount = $derived(
    !disabled &&
      !busy &&
      uiState !== "enabled" &&
      status?.configured === true &&
      status.preference_enabled === true &&
      status.devices.length > 0
  );
</script>

<Card class="push-notifications-card">
  <div class="push-notifications-card__heading">
    <span class="push-notifications-card__icon"><Megaphone size={26} /></span>
    <div>
      <h2>{t("wa_push_title", {}, "Browser notifications")}</h2>
      <p>
        {t(
          "wa_push_description",
          {},
          "Service/support updates and news in this browser. One switch covers both; Telegram and email preferences stay unchanged."
        )}
      </p>
    </div>
    {#if uiState === "enabled"}
      <Badge variant="success">{t("wa_push_enabled", {}, "On")}</Badge>
    {/if}
  </div>

  {#if uiState === "enabled"}
    <p class="push-notifications-card__status">
      {t("wa_push_enabled_status", {}, "Active — this device receives notifications.")}
    </p>
    <Button variant="outline" size="sm" disabled={busy || disabled} onclick={disableAll}>
      {t("wa_push_disable", {}, "Turn off browser notifications")}
    </Button>
  {:else if canEnable}
    {#if !currentPushDeviceConfirmed(status)}
      <p class="push-notifications-card__status">
        {t(
          "wa_push_not_enabled",
          {},
          "This device is not enabled. Confirm to receive notifications."
        )}
      </p>
    {/if}
    <Button disabled={busy} onclick={enable}>
      {busy
        ? t("wa_push_enable_busy", {}, "Enabling…")
        : t("wa_push_enable", {}, "Enable browser notifications")}
    </Button>
    <p class="push-notifications-card__hint">
      {t(
        "wa_push_permission_hint",
        {},
        "The browser will ask for permission once. We only subscribe after you press the button."
      )}
    </p>
  {:else if guidance}
    <p class="push-notifications-card__guidance">{guidance}</p>
  {/if}

  {#if canDisableAccount}
    <Button variant="outline" size="sm" disabled={busy || disabled} onclick={disableAll}>
      {t("wa_push_disable", {}, "Turn off browser notifications")}
    </Button>
  {/if}

  {#if uiState === "enabled" || (status?.devices?.length ?? 0) > 0}
    <p class="push-notifications-card__hint">
      {t(
        "wa_push_scope_note",
        {},
        "Covers service/support updates and news/promotions. Security codes and passwords are never sent in notifications."
      )}
    </p>
  {/if}

  {#if status?.configured && status.devices.length > 0}
    <div class="push-notifications-card__devices">
      <h3>{t("wa_push_devices_title", {}, "Devices")}</h3>
      <ul>
        {#each status.devices as device (device.id)}
          <li>
            <div class="push-notifications-card__device-copy">
              <strong>{currentDeviceLabel(device)}</strong>
              {#if device.current}
                <Badge variant="outline">{t("wa_push_current_device", {}, "This device")}</Badge>
              {/if}
            </div>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy || disabled}
              onclick={() => removeDevice(device.id, device.current)}
            >
              {device.current
                ? t("wa_push_device_remove", {}, "Disable on this device")
                : t("wa_push_device_remove_other", {}, "Remove device")}
            </Button>
          </li>
        {/each}
      </ul>
    </div>
  {:else if status?.configured && uiState === "enabled"}
    <p class="push-notifications-card__hint">
      {t("wa_push_no_devices", {}, "No subscribed devices yet.")}
    </p>
  {/if}

  {#if permission === "denied" && uiState === "enabled"}
    <p class="push-notifications-card__guidance">
      {t(
        "wa_push_denied",
        {},
        "Notifications are blocked for this site. Allow them in the browser's site settings, then reload this page."
      )}
    </p>
  {/if}

  {#if message}
    <p class="push-notifications-card__message" class:is-error={messageIsError}>{message}</p>
  {/if}

  {#if initialized && status?.configured}
    <p class="push-notifications-card__hint">
      {t(
        "wa_push_sync_note",
        {},
        "This browser is linked to your account. After switching accounts, it is rebound on the next visit."
      )}
    </p>
  {/if}
</Card>

<style>
  :global(.push-notifications-card) {
    display: grid;
    gap: 14px;
    padding: 18px;
  }

  .push-notifications-card__heading {
    display: flex;
    align-items: flex-start;
    gap: 12px;
  }

  .push-notifications-card__heading > div {
    display: grid;
    flex: 1;
    gap: 4px;
    min-width: 0;
  }

  .push-notifications-card__heading h2,
  .push-notifications-card__devices h3 {
    margin: 0;
    font-size: 16px;
  }

  .push-notifications-card__heading p,
  .push-notifications-card__hint,
  .push-notifications-card__guidance,
  .push-notifications-card__status,
  .push-notifications-card__message {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }

  .push-notifications-card__icon {
    display: grid;
    flex: 0 0 auto;
    place-items: center;
    width: 42px;
    height: 42px;
    border-radius: 12px;
    background: var(--panel-2);
    color: var(--accent);
  }

  .push-notifications-card__message {
    color: var(--accent);
  }

  .push-notifications-card__message.is-error {
    color: var(--danger);
  }

  .push-notifications-card__devices {
    display: grid;
    gap: 8px;
  }

  .push-notifications-card__devices ul {
    display: grid;
    margin: 0;
    padding: 0;
    gap: 8px;
    list-style: none;
  }

  .push-notifications-card__devices li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-inner, var(--radius));
    background: var(--panel-2);
  }

  .push-notifications-card__device-copy {
    display: flex;
    align-items: center;
    min-width: 0;
    gap: 7px;
  }

  .push-notifications-card__device-copy strong {
    overflow: hidden;
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  @media (max-width: 640px) {
    .push-notifications-card__devices li {
      align-items: flex-start;
      flex-direction: column;
    }
  }
</style>

<script lang="ts">
  import { onMount } from "svelte";
  import { ChevronRight, Download, Smartphone } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Card from "$components/ui/card.svelte";
  import Dialog from "$components/ui/dialog.svelte";
  import PushNotificationsCard from "./push/PushNotificationsCard.svelte";
  import PwaInstallDialog from "./pwa/PwaInstallDialog.svelte";
  import { getPwaInstallSnapshot, reportPwaInstallClicked } from "$lib/webapp/pwaInstall.svelte.js";
  import { loadBrowserPushStatus, type BrowserPushStatus } from "$lib/webapp/webPush.js";
  import { unwrap, type ApiClient } from "$lib/webapp/publicApi.js";
  import type { NotificationPreferences } from "$lib/webapp/notificationPreferences.js";
  import type { Translate } from "$lib/webapp/types.js";

  // The old below-the-fold card and the Home shortcut have separate dismissals.
  const DISMISSED_KEY = "minishop.account-setup-shortcut.dismissed.v1";
  let dismissedInSession = false;

  type Props = {
    api: ApiClient["api"];
    preferences: NotificationPreferences;
    t: Translate;
    onPreferencesSaved?: (preferences: NotificationPreferences) => void;
    surface?: "home" | "settings";
  };

  let {
    api,
    preferences,
    t,
    onPreferencesSaved = () => {},
    surface = "settings",
  }: Props = $props();
  let dismissed = $state(false);
  let setupOpen = $state(false);
  let dialogOpen = $state(false);
  let installPromptAccepted = $state(false);
  let installStatus = $state<HTMLParagraphElement | null>(null);
  let setupTrigger: HTMLElement | null = null;
  let installTrigger: HTMLElement | null = null;
  let pushPreference = $state(false);
  let browserPushStatus = $state<BrowserPushStatus | null>(null);
  const snapshot = $derived(getPwaInstallSnapshot());
  const appInstalled = $derived(snapshot.state === "installed");
  const setupDescription = $derived(appInstalled ? "" : t("wa_setup_description"));

  onMount(() => {
    dismissed = surface === "home" && (dismissedInSession || safeStorageRead() === "1");
    if (!dismissed) void refreshBrowserPushStatus();
  });

  $effect(() => {
    if (dialogOpen || !installTrigger) return;
    const target = installTrigger.isConnected ? installTrigger : installStatus;
    target?.focus({ preventScroll: true });
    installTrigger = null;
  });

  $effect(() => {
    pushPreference = preferences.push_notifications_enabled;
  });

  function safeStorageRead(): string | null {
    try {
      const value = window.localStorage.getItem(DISMISSED_KEY);
      if (value) return value;
    } catch {
      // Fall back to session storage when persistent storage is unavailable.
    }
    try {
      return window.sessionStorage.getItem(DISMISSED_KEY);
    } catch {
      return null;
    }
  }

  async function refreshBrowserPushStatus(): Promise<void> {
    const previous = browserPushStatus;
    const next = await loadBrowserPushStatus(api);
    // A passive check can finish after a newer opt-in or device change.
    if (browserPushStatus === previous && (next.status || !previous?.status)) {
      browserPushStatus = next;
    }
  }

  async function updatePreferences(next: NotificationPreferences): Promise<boolean> {
    const previous = pushPreference;
    pushPreference = next.push_notifications_enabled;
    try {
      const result = unwrap(
        await api("/account/notification-preferences", {
          method: "POST",
          body: JSON.stringify(next),
        })
      );
      const saved = result.notification_preferences;
      pushPreference = Boolean(saved.push_notifications_enabled);
      onPreferencesSaved(saved);
      return true;
    } catch {
      pushPreference = previous;
      return false;
    }
  }

  function openSetup(event: MouseEvent): void {
    if (event.currentTarget instanceof HTMLElement) setupTrigger = event.currentTarget;
    if (browserPushStatus) void refreshBrowserPushStatus();
    setupOpen = true;
  }

  function closeSetup(): void {
    if (dialogOpen) {
      // Escape can reach the setup dialog before the install dialog takes focus.
      dialogOpen = false;
      return;
    }
    setupOpen = false;
    setupTrigger?.focus({ preventScroll: true });
  }

  function openInstall(event: MouseEvent): void {
    if (event.currentTarget instanceof HTMLElement) installTrigger = event.currentTarget;
    reportPwaInstallClicked(surface);
    dialogOpen = true;
  }

  function dismiss(): void {
    setupOpen = false;
    dismissed = true;
    dismissedInSession = true;
    try {
      window.localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      try {
        window.sessionStorage.setItem(DISMISSED_KEY, "1");
      } catch {
        // Keep this page-session dismissal when browser storage is unavailable.
      }
    }
  }
</script>

{#snippet setupControls()}
  <div class="setup-controls">
    {#if appInstalled}
      <p bind:this={installStatus} class="setup-note setup-install-status" tabindex="-1">
        {t("wa_pwa_install_installed")}
      </p>
    {:else}
      <Button class="wide" data-webapp-action="setup-install" onclick={openInstall}>
        <Download size={16} />{t("wa_setup_install_action")}
      </Button>
      <p class="setup-note">{t("wa_setup_install_note")}</p>
      {#if installPromptAccepted}
        <p class="setup-note" role="status">{t("wa_setup_install_wait")}</p>
      {/if}
    {/if}
    <PushNotificationsCard
      {api}
      {t}
      bind:browserStatus={browserPushStatus}
      loadOnMount={false}
      preferences={{
        ...preferences,
        push_notifications_enabled: pushPreference,
      }}
      onPreferencesChange={updatePreferences}
    />
  </div>
{/snippet}

{#if !dismissed}
  {#if surface === "home"}
    <Button
      class="setup-shortcut"
      variant="outline"
      data-webapp-action="open-account-setup"
      aria-haspopup="dialog"
      onclick={openSetup}
    >
      <span class="setup-icon" aria-hidden="true"><Smartphone size={19} /></span>
      <span class="setup-shortcut-copy">
        <strong>{t("wa_setup_short_title")}</strong>
        <small>
          {#if !appInstalled}{t("wa_setup_install_action")} ·
          {/if}{t("wa_push_title")}
        </small>
      </span>
      <span class="setup-shortcut-arrow" aria-hidden="true"><ChevronRight size={18} /></span>
    </Button>
    <Dialog
      open={setupOpen}
      title={t("wa_setup_short_title")}
      description={setupDescription}
      closeLabel={t("wa_close")}
      onclose={closeSetup}
      class="account-setup-dialog"
      portal
    >
      {@render setupControls()}
      {#snippet footer()}
        <Button variant="ghost" size="sm" data-webapp-action="setup-dismiss" onclick={dismiss}>
          {t("wa_setup_dismiss")}
        </Button>
      {/snippet}
    </Dialog>
  {:else}
    <Card class="account-setup-card">
      <div class="setup-heading">
        <span class="setup-icon" aria-hidden="true"><Smartphone size={19} /></span>
        <div>
          <h2>{t("wa_setup_title")}</h2>
          {#if setupDescription}<p>{setupDescription}</p>{/if}
        </div>
      </div>
      {@render setupControls()}
    </Card>
  {/if}
  <PwaInstallDialog
    bind:open={dialogOpen}
    signalScreen={surface}
    {t}
    onPromptResult={(result) => {
      installPromptAccepted = result === "accepted";
    }}
  />
{/if}

<style>
  :global(.account-setup-card),
  .setup-controls {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .setup-heading {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr);
    gap: 10px;
    align-items: center;
  }
  .setup-icon {
    display: grid;
    width: 38px;
    height: 38px;
    place-items: center;
    border-radius: var(--radius-inner);
    background: color-mix(in srgb, var(--accent) 15%, var(--panel-2));
    color: var(--accent);
  }
  :global(.btn.setup-shortcut) {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr) auto;
    gap: 10px;
    padding: 8px 12px;
    text-align: start;
  }
  .setup-shortcut-copy {
    display: grid;
    gap: 4px;
    overflow-wrap: anywhere;
  }
  .setup-shortcut-copy small {
    color: var(--muted);
    font-size: 12px;
    font-weight: 500;
    line-height: 1.3;
  }
  .setup-shortcut-arrow {
    display: flex;
    color: var(--muted);
  }
  .setup-heading h2 {
    margin: 0;
    color: var(--text);
    font-size: 15px;
  }
  .setup-heading p,
  .setup-note {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.4;
  }
</style>

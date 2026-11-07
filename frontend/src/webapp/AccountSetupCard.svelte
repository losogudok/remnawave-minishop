<script lang="ts">
  import { onMount } from "svelte";
  import { Download, Gift, X } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Card from "$components/ui/card.svelte";
  import PushNotificationsCard from "./push/PushNotificationsCard.svelte";
  import PwaInstallDialog from "./pwa/PwaInstallDialog.svelte";
  import {
    getPwaInstallSnapshot,
    isPwaStandaloneLaunch,
    reportPwaInstallClicked,
  } from "$lib/webapp/pwaInstall.svelte.js";
  import { getCurrentPushSubscription } from "$lib/webapp/webPush.js";
  import { unwrap, type ApiClient } from "$lib/webapp/publicApi.js";
  import type { NotificationPreferences } from "$lib/webapp/notificationPreferences.js";
  import type { Translate } from "$lib/webapp/types.js";

  const DISMISSED_KEY = "minishop.account-setup.dismissed.v1";
  let dismissedInSession = false;
  type SetupRewardsStatus = {
    install_days: number;
    push_days: number;
    push_available: boolean;
    install_completed: boolean;
    push_completed: boolean;
    install_state: string;
    push_state: string;
    install_reward_days: number;
    push_reward_days: number;
    install_end_date: string | null;
    push_end_date: string | null;
  };
  type Props = {
    api: ApiClient["api"];
    preferences: NotificationPreferences;
    t: Translate;
    dismissible?: boolean;
    activeAccess?: boolean;
    onPreferencesSaved?: (preferences: NotificationPreferences) => void;
    signalScreen?: "home" | "settings";
  };

  let {
    api,
    preferences,
    t,
    dismissible = false,
    activeAccess = false,
    onPreferencesSaved = () => {},
    signalScreen = "home",
  }: Props = $props();
  let dismissed = $state(false);
  let dialogOpen = $state(false);
  let message = $state("");
  let installRewardMessage = $state("");
  let pushRewardMessage = $state("");
  let pushPreference = $state(false);
  let rewardStatus = $state<SetupRewardsStatus | null>(null);
  let setupRewardsInitialized = $state(false);
  let installClaimAttempted = false;
  let pushClaimAttempted = false;
  const snapshot = $derived(getPwaInstallSnapshot());
  const installOfferDays = $derived(
    activeAccess &&
      rewardStatus &&
      !rewardStatus.install_completed &&
      ["guidance-only", "prompt-available", "installed", "unknown"].includes(snapshot.state) &&
      snapshot.platform !== "other"
      ? rewardStatus.install_days
      : 0
  );
  const pushOfferDays = $derived(
    activeAccess && rewardStatus?.push_available && !rewardStatus.push_completed
      ? rewardStatus.push_days
      : 0
  );
  const showAfterActivation = $derived(
    !activeAccess &&
      Boolean(
        rewardStatus &&
        ((rewardStatus.install_days > 0 &&
          (!rewardStatus.install_completed || rewardStatus.install_reward_days === 0)) ||
          (rewardStatus.push_days > 0 &&
            (!rewardStatus.push_completed || rewardStatus.push_reward_days === 0)))
      )
  );

  onMount(() => {
    dismissed = dismissible && (dismissedInSession || safeStorageRead() === "1");
    void initializeSetupRewards();
  });

  $effect(() => {
    pushPreference = preferences.push_notifications_enabled;
  });

  $effect(() => {
    const status = rewardStatus;
    if (!setupRewardsInitialized || !activeAccess || !status) return;
    if (
      status.install_completed &&
      status.install_state !== "confirmed" &&
      !installClaimAttempted
    ) {
      installClaimAttempted = true;
      void claimReward("install");
    }
    if (status.push_completed && status.push_state !== "confirmed" && !pushClaimAttempted) {
      pushClaimAttempted = true;
      void claimReward("push");
    }
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

  function isStandaloneLaunch(): boolean {
    return snapshot.state === "installed" && isPwaStandaloneLaunch();
  }

  async function initializeSetupRewards(): Promise<void> {
    const status = await loadRewardStatus();
    if (isStandaloneLaunch()) {
      await reportInstallCompletion();
    }
    if (!status?.push_completed && preferences.push_notifications_enabled) {
      await claimCurrentPushCompletion();
    }
    setupRewardsInitialized = true;
  }

  async function loadRewardStatus(): Promise<SetupRewardsStatus | null> {
    try {
      const status = unwrap(await api("/setup-rewards/status")) as SetupRewardsStatus;
      rewardStatus = status;
      if (status.install_state === "confirmed" && status.install_reward_days > 0) {
        installRewardMessage = confirmedMessage(
          status.install_reward_days,
          status.install_end_date
        );
      }
      if (status.push_state === "confirmed" && status.push_reward_days > 0) {
        pushRewardMessage = confirmedMessage(status.push_reward_days, status.push_end_date);
      }
      return status;
    } catch {
      return null;
    }
  }

  async function reportInstallCompletion(): Promise<void> {
    try {
      unwrap(
        await api("/setup-rewards/install-evidence", {
          method: "POST",
          body: JSON.stringify({ standalone: true }),
        })
      );
      rewardStatus = {
        ...(rewardStatus || emptyRewardStatus()),
        install_completed: true,
        install_state: "completed",
      };
    } catch {
      // Setup and VPN access remain available when reward reporting is offline.
    }
  }

  async function claimReward(rewardType: "install" | "push", endpoint?: string): Promise<void> {
    if (rewardType === "install") installClaimAttempted = activeAccess;
    else pushClaimAttempted = activeAccess;
    try {
      const result = unwrap(
        await api("/setup-rewards/claim", {
          method: "POST",
          body: JSON.stringify({
            rewardType,
            ...(endpoint ? { endpoint } : {}),
          }),
        })
      );
      const state = String(result.state || "");
      const days = Number(result.days || 0);
      const endDate = typeof result.end_date === "string" ? result.end_date : null;
      const current = rewardStatus || emptyRewardStatus();
      if (rewardType === "install") {
        rewardStatus = {
          ...current,
          install_completed: true,
          install_state: state,
          install_reward_days: days,
          install_end_date: endDate,
        };
        if (state === "confirmed" && days > 0 && endDate) {
          installRewardMessage = confirmedMessage(days, endDate);
        } else if (state === "pending" && days > 0) {
          installRewardMessage = t("wa_setup_reward_pending");
        }
      } else {
        rewardStatus = {
          ...current,
          push_completed: true,
          push_state: state,
          push_reward_days: days,
          push_end_date: endDate,
        };
        if (state === "confirmed" && days > 0 && endDate) {
          pushRewardMessage = confirmedMessage(days, endDate);
        } else if (state === "pending" && days > 0) {
          pushRewardMessage = t("wa_setup_reward_pending");
        }
      }
    } catch (error) {
      const code =
        typeof error === "object" && error !== null && "error" in error
          ? String((error as { error?: unknown }).error || "")
          : "";
      if (rewardType === "push" && code === "active_subscription_required" && !activeAccess) {
        pushClaimAttempted = false;
        rewardStatus = {
          ...(rewardStatus || emptyRewardStatus()),
          push_completed: true,
          push_state: "completed",
        };
        return;
      }
      message = t("wa_setup_reward_unavailable");
    }
  }

  async function onPushEnabled(): Promise<void> {
    const subscription = await getCurrentPushSubscription();
    const endpoint = subscription?.endpoint || "";
    if (!endpoint) return;
    await claimReward("push", endpoint);
  }

  async function claimCurrentPushCompletion(): Promise<void> {
    const subscription = await getCurrentPushSubscription();
    if (subscription?.endpoint) await claimReward("push", subscription.endpoint);
  }

  function confirmedMessage(days: number, endDate: string | null): string {
    if (!endDate) return "";
    return t("wa_setup_reward_confirmed", {
      days,
      date: new Date(endDate).toLocaleDateString(),
    });
  }

  function emptyRewardStatus(): SetupRewardsStatus {
    return {
      install_days: 0,
      push_days: 0,
      push_available: false,
      install_completed: false,
      push_completed: false,
      install_state: "not_started",
      push_state: "not_started",
      install_reward_days: 0,
      push_reward_days: 0,
      install_end_date: null,
      push_end_date: null,
    };
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

  function openInstall(): void {
    reportPwaInstallClicked(signalScreen);
    dialogOpen = true;
  }

  function dismiss(): void {
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

{#if !dismissed}
  <Card class="account-setup-card">
    <div class="setup-heading">
      <span class="setup-icon"><Gift size={19} /></span>
      <div>
        <h2>{t("wa_setup_title")}</h2>
        <p>{t("wa_setup_description")}</p>
      </div>
      {#if dismissible}
        <Button
          data-webapp-action="setup-dismiss"
          variant="icon"
          size="icon-sm"
          aria-label={t("wa_setup_dismiss")}
          onclick={dismiss}
        >
          <X size={16} />
        </Button>
      {/if}
    </div>
    <div class="setup-actions">
      <Button data-webapp-action="setup-install" variant="secondary" onclick={openInstall}>
        <Download size={16} />{t("wa_setup_install_action")}
      </Button>
    </div>
    {#if installOfferDays > 0}
      <p class="setup-offer">{t("wa_setup_install_offer", { days: installOfferDays })}</p>
    {/if}
    {#if showAfterActivation}
      <p class="setup-note">{t("wa_setup_reward_after_activation")}</p>
    {/if}
    <p class="setup-note">{t("wa_setup_install_note")}</p>
    <PushNotificationsCard
      {api}
      {t}
      preferences={{
        ...preferences,
        push_notifications_enabled: pushPreference,
      }}
      onPreferencesChange={updatePreferences}
      onEnabled={onPushEnabled}
      rewardDays={pushOfferDays}
    />
    {#if installRewardMessage}<p class="setup-reward" role="status">{installRewardMessage}</p>{/if}
    {#if pushRewardMessage}<p class="setup-reward" role="status">{pushRewardMessage}</p>{/if}
    {#if message}<p class="setup-error" role="status">{message}</p>{/if}
  </Card>
  <PwaInstallDialog
    bind:open={dialogOpen}
    {signalScreen}
    {t}
    onPromptResult={(result) => {
      if (result === "accepted") message = t("wa_setup_install_wait");
    }}
  />
{/if}

<style>
  :global(.account-setup-card) {
    display: grid;
    gap: 10px;
  }
  .setup-heading {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr) auto;
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
  .setup-heading h2 {
    margin: 0;
    color: var(--text);
    font-size: 15px;
  }
  .setup-heading p,
  .setup-note,
  .setup-offer,
  .setup-reward,
  .setup-error {
    margin: 3px 0 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.4;
  }
  .setup-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .setup-reward {
    color: var(--success, var(--accent));
  }
  .setup-offer {
    color: var(--accent);
  }
  .setup-error {
    color: var(--danger);
  }
</style>

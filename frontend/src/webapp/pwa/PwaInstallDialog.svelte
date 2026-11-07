<script lang="ts">
  import { Download, Share2, Smartphone, TriangleAlert } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Dialog from "$components/ui/dialog.svelte";
  import {
    getPwaInstallSnapshot,
    promptPwaInstall,
    setPwaInstallAnalyticsScreen,
  } from "$lib/webapp/pwaInstall.svelte.js";
  import type { Translate } from "$lib/webapp/types.js";

  type PromptResult = "accepted" | "dismissed" | "unavailable" | "";

  type Props = {
    open?: boolean;
    signalScreen?: "home" | "settings";
    t: Translate;
    onPromptResult?: (result: "accepted" | "dismissed" | "unavailable") => void;
  };

  let {
    open = $bindable(false),
    signalScreen = "home",
    t,
    onPromptResult = () => {},
  }: Props = $props();

  let busy = $state(false);
  let result = $state<PromptResult>("");
  const snapshot = $derived(getPwaInstallSnapshot());
  const showPromptButton = $derived(snapshot.state === "prompt-available" && snapshot.canPrompt);
  const stepsKey = $derived(
    snapshot.platform === "ios"
      ? "wa_pwa_install_ios_steps"
      : snapshot.platform === "android"
        ? "wa_pwa_install_android_steps"
        : "wa_pwa_install_desktop_steps"
  );

  $effect(() => {
    if (!open) return;
    setPwaInstallAnalyticsScreen(signalScreen);
    result = "";
  });

  async function install(): Promise<void> {
    if (busy) return;
    busy = true;
    try {
      const outcome = await promptPwaInstall();
      result = outcome;
      onPromptResult(outcome);
    } finally {
      busy = false;
    }
  }
</script>

<Dialog
  {open}
  title={t("wa_pwa_install_title")}
  description={t("wa_pwa_install_hint")}
  closeLabel={t("wa_close")}
  onclose={() => (open = false)}
  class="pwa-install-dialog"
  portal
>
  <div class="pwa-install-body">
    {#if snapshot.state === "installed"}
      <div class="pwa-install-state">
        <Smartphone size={20} />
        <p>{t("wa_pwa_install_installed")}</p>
      </div>
    {:else if snapshot.state === "pwa-disabled"}
      <div class="pwa-install-state">
        <TriangleAlert size={20} />
        <p>{t("wa_pwa_install_unsupported")}</p>
      </div>
    {:else}
      {#if snapshot.state === "embedded"}
        <div class="pwa-install-state">
          <Smartphone size={20} />
          <p>{t("wa_pwa_install_embedded")}</p>
        </div>
      {/if}

      {#if showPromptButton}
        <Button class="wide pwa-install-action" onclick={install} disabled={busy}>
          <Download size={17} />
          {t("wa_pwa_install_cta")}
        </Button>
      {/if}

      {#if !showPromptButton}
        <div class="pwa-install-steps">
          {#if snapshot.platform === "ios"}
            <Share2 size={20} />
          {:else}
            <Smartphone size={20} />
          {/if}
          <p>{t(stepsKey)}</p>
        </div>
      {/if}

      {#if result === "accepted"}
        <p class="pwa-install-status" role="status">{t("wa_pwa_install_accepted")}</p>
      {:else if result === "unavailable"}
        <p class="pwa-install-status pwa-install-status-error" role="alert">
          {t("wa_pwa_install_confirmation_unavailable")}
        </p>
      {/if}

      <p class="pwa-install-note">{t("wa_pwa_install_reset_note")}</p>
    {/if}
  </div>
</Dialog>

<style>
  :global(.pwa-install-dialog) {
    width: min(100%, 480px);
  }

  .pwa-install-body {
    display: grid;
    gap: 12px;
  }

  .pwa-install-body p {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.5;
  }

  .pwa-install-state {
    display: grid;
    grid-template-columns: 24px minmax(0, 1fr);
    gap: 10px;
    align-items: start;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-inner);
    background: var(--surface-muted);
  }

  .pwa-install-state :global(svg) {
    color: var(--accent);
  }

  :global(.pwa-install-action) {
    min-height: 46px;
  }

  .pwa-install-steps {
    display: grid;
    grid-template-columns: 24px minmax(0, 1fr);
    gap: 10px;
    align-items: start;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-inner);
    background: var(--surface-muted);
  }

  .pwa-install-steps :global(svg) {
    color: var(--accent);
  }

  .pwa-install-body .pwa-install-status {
    color: var(--success-text);
  }

  .pwa-install-body .pwa-install-status-error {
    color: var(--warning-text);
  }

  .pwa-install-body .pwa-install-note {
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 12px;
  }

  @media (max-width: 360px) {
    .pwa-install-state,
    .pwa-install-steps {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>

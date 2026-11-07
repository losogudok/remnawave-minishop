<script lang="ts">
  import { onMount } from "svelte";
  import { Download, Smartphone, X } from "$components/ui/icons.js";
  import Button from "$components/ui/button.svelte";
  import Card from "$components/ui/card.svelte";
  import {
    getPwaInstallSnapshot,
    isPwaInstallAvailable,
    markPwaOfferDismissed,
    markPwaOfferShown,
    reportPwaInstallClicked,
    shouldShowPwaInstallOffer,
  } from "$lib/webapp/pwaInstall.svelte.js";
  import type { Translate } from "$lib/webapp/types.js";
  import PwaInstallDialog from "./PwaInstallDialog.svelte";

  type Props = {
    signalScreen?: "home" | "settings";
    persistent?: boolean;
    t: Translate;
  };

  let { signalScreen = "home", persistent = false, t }: Props = $props();

  let offered = $state(false);
  let dialogOpen = $state(false);
  const snapshot = $derived(getPwaInstallSnapshot());
  const eligible = $derived(isPwaInstallAvailable(snapshot.state));

  onMount(() => {
    if (!shouldShowPwaInstallOffer()) return;
    markPwaOfferShown();
    offered = true;
  });

  function openDialog(): void {
    reportPwaInstallClicked(signalScreen);
    dialogOpen = true;
  }

  function dismiss(): void {
    markPwaOfferDismissed();
    offered = false;
  }

  function handlePromptResult(result: "accepted" | "dismissed" | "unavailable"): void {
    if (persistent) return;
    if (result === "accepted" || result === "dismissed") offered = false;
  }
</script>

{#if (persistent || offered) && eligible}
  <Card class="pwa-install-offer">
    <div class="pwa-install-offer-icon">
      <Smartphone size={20} />
    </div>
    <div class="pwa-install-offer-copy">
      <strong>{t("wa_pwa_install_offer_title")}</strong>
      <small>{t("wa_pwa_install_offer_hint")}</small>
    </div>
    {#if !persistent}
      <Button
        variant="icon"
        size="icon-sm"
        aria-label={t("wa_pwa_install_dismiss")}
        onclick={dismiss}
      >
        <X size={16} />
      </Button>
    {/if}
    <div class="pwa-install-offer-actions">
      <Button size="sm" onclick={openDialog}>
        <Download size={15} />
        {t("wa_pwa_install_cta")}
      </Button>
    </div>
  </Card>
  <PwaInstallDialog bind:open={dialogOpen} {signalScreen} {t} onPromptResult={handlePromptResult} />
{/if}

<style>
  :global(.pwa-install-offer) {
    display: grid;
    grid-template-columns: 38px minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
    width: 100%;
    border-color: color-mix(in srgb, var(--accent) 34%, var(--border));
    background:
      linear-gradient(
        135deg,
        color-mix(in srgb, var(--accent) 9%, transparent),
        color-mix(in srgb, var(--accent) 3%, transparent)
      ),
      var(--panel);
  }

  .pwa-install-offer-icon {
    display: grid;
    width: 38px;
    height: 38px;
    place-items: center;
    border-radius: var(--radius-inner);
    background: color-mix(in srgb, var(--accent) 15%, var(--panel-2));
    color: var(--accent);
  }

  .pwa-install-offer-copy {
    display: grid;
    min-width: 0;
    gap: 3px;
  }

  .pwa-install-offer-copy strong {
    color: var(--text);
    font-size: 14px;
    line-height: 1.2;
  }

  .pwa-install-offer-copy small {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.35;
  }

  .pwa-install-offer-actions {
    grid-column: 1 / -1;
    display: grid;
  }

  .pwa-install-offer-actions :global(.btn) {
    min-width: 0;
    width: 100%;
  }

  @media (min-width: 480px) {
    .pwa-install-offer-actions {
      grid-column: 2 / 3;
      justify-self: start;
    }

    .pwa-install-offer-actions :global(.btn) {
      width: auto;
    }
  }

  @media (min-width: 1024px) {
    :global(.pwa-install-offer) {
      max-width: 1080px;
      margin-left: auto;
      margin-right: auto;
    }
  }
</style>

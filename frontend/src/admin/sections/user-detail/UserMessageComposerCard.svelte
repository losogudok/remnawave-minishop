<script lang="ts">
  import {
    AdminButton,
    AdminSectionHeader,
    AdminSelect,
  } from "$components/patterns/admin/index.js";
  import { Input } from "$components/ui/index.js";
  import { Plus, Send, Trash2 } from "$components/ui/icons.js";
  import MessageComposer from "$lib/admin/components/MessageComposer.svelte";
  import { getBroadcastStore } from "$lib/admin/context";
  import type {
    BroadcastButtonDraft,
    BroadcastButtonKind,
  } from "$lib/admin/stores/broadcastStore.svelte";

  import type { TranslateFn } from "./userDetailTypes";

  let {
    at,
    userId,
    hasEmail = false,
  }: {
    at: TranslateFn;
    userId: number | null;
    hasEmail?: boolean;
  } = $props();

  const MAX_BUTTONS = 4;
  // Shortcodes are advertised by the backend and shared with the broadcast
  // screen, so the composer offers the same set in both places.
  const broadcastStore = getBroadcastStore();
  const shortcodes = $derived(broadcastStore.broadcastShortcodes);

  let text = $state("");
  let emailSubject = $state("");
  let telegramEnabled = $state(true);
  let emailEnabled = $state(false);
  let buttons = $state<BroadcastButtonDraft[]>([]);
  let busy = $state(false);
  let result = $state<{ kind: "ok" | "error"; message: string } | null>(null);

  const kindItems = $derived([
    { value: "url", label: at("broadcast_button_kind_url", {}, "Link") },
    {
      value: "promo_bot",
      label: at("broadcast_button_kind_promo_bot", {}, "Promo code — in bot"),
    },
    {
      value: "promo_webapp",
      label: at("broadcast_button_kind_promo_webapp", {}, "Promo code — in web app"),
    },
  ]);
  const channels = $derived(
    [telegramEnabled ? "telegram" : "", emailEnabled ? "email" : ""].filter(Boolean)
  );
  const buttonsValid = $derived(
    buttons.every(
      (button) =>
        button.label.trim() && (button.kind === "url" ? button.url.trim() : button.promoCode.trim())
    )
  );
  const canSend = $derived(
    !busy && userId !== null && Boolean(text.trim()) && channels.length > 0 && buttonsValid
  );

  // An email-only draft is impossible for a customer with no linked address,
  // so the toggle stays off and disabled rather than failing on send.
  $effect(() => {
    if (!hasEmail && emailEnabled) emailEnabled = false;
  });

  function addButton(): void {
    if (buttons.length >= MAX_BUTTONS) return;
    buttons = [
      ...buttons,
      { id: Date.now() + buttons.length, kind: "url", label: "", url: "", promoCode: "" },
    ];
  }

  function updateButton(index: number, fields: Partial<BroadcastButtonDraft>): void {
    buttons = buttons.map((button, position) =>
      position === index ? { ...button, ...fields } : button
    );
  }

  function removeButton(index: number): void {
    buttons = buttons.filter((_, position) => position !== index);
  }

  async function send(): Promise<void> {
    if (userId === null) return;
    busy = true;
    result = null;
    const error = await broadcastStore.sendToUser({
      userId,
      text,
      channels,
      emailSubject,
      buttons,
    });
    if (error === null) {
      text = "";
      emailSubject = "";
      buttons = [];
      result = { kind: "ok", message: at("user_message_sent", {}, "Message sent") };
    } else {
      result = { kind: "error", message: error };
    }
    busy = false;
  }
</script>

<section class="admin-user-action-sheet admin-user-action-sheet--message">
  <AdminSectionHeader
    title={at("user_message_title", {}, "Send a message")}
    description={at(
      "user_message_hint",
      {},
      "Rich text with shortcodes and buttons, delivered to Telegram and email."
    )}
  />
  <div class="admin-user-action-sheet-body">
    <MessageComposer
      value={text}
      onInput={(next) => (text = next)}
      {shortcodes}
      onRequestShortcodes={broadcastStore.loadShortcodes}
      {at}
      placeholder={at("user_placeholder_msg", {}, "Message text")}
    />

    <div class="admin-user-message-channels">
      <label class="admin-check">
        <input type="checkbox" bind:checked={telegramEnabled} />
        {at("broadcast_channel_telegram", {}, "Telegram")}
      </label>
      <label class="admin-check" class:is-disabled={!hasEmail}>
        <input type="checkbox" bind:checked={emailEnabled} disabled={!hasEmail} />
        {at("broadcast_channel_email", {}, "Email")}
        {#if !hasEmail}
          <small class="admin-muted">{at("user_message_no_email", {}, "no linked address")}</small>
        {/if}
      </label>
    </div>

    {#if emailEnabled}
      <Input
        class="input"
        type="text"
        value={emailSubject}
        placeholder={at("broadcast_email_subject", {}, "Email subject")}
        aria-label={at("broadcast_email_subject", {}, "Email subject")}
        oninput={(event) => (emailSubject = (event.currentTarget as HTMLInputElement).value)}
      />
    {/if}

    <div class="admin-user-message-buttons">
      <div class="admin-user-message-buttons-head">
        <span>{at("broadcast_buttons_label", {}, "Buttons")}</span>
        <AdminButton
          size="sm"
          variant="ghost"
          disabled={buttons.length >= MAX_BUTTONS}
          onclick={addButton}
        >
          <Plus size={14} />
          {at("broadcast_button_add", {}, "Add button")}
        </AdminButton>
      </div>
      {#each buttons as button, index (index)}
        <div class="admin-user-message-button">
          <AdminSelect
            value={button.kind}
            items={kindItems}
            ariaLabel={at("broadcast_buttons_label", {}, "Buttons")}
            onValueChange={(value) => updateButton(index, { kind: value as BroadcastButtonKind })}
          />
          <Input
            class="input"
            type="text"
            value={button.label}
            placeholder={at("broadcast_button_label", {}, "Button label")}
            aria-label={at("broadcast_button_label", {}, "Button label")}
            oninput={(event) =>
              updateButton(index, {
                label: (event.currentTarget as HTMLInputElement).value,
              })}
          />
          {#if button.kind === "url"}
            <Input
              class="input"
              type="text"
              value={button.url}
              placeholder="https://"
              aria-label={at("broadcast_button_kind_url", {}, "Link")}
              oninput={(event) =>
                updateButton(index, {
                  url: (event.currentTarget as HTMLInputElement).value,
                })}
            />
          {:else}
            <Input
              class="input"
              type="text"
              value={button.promoCode}
              placeholder={at("broadcast_button_promo_code", {}, "Promo code")}
              aria-label={at("broadcast_button_promo_code", {}, "Promo code")}
              oninput={(event) =>
                updateButton(index, {
                  promoCode: (event.currentTarget as HTMLInputElement).value,
                })}
            />
          {/if}
          <AdminButton
            variant="dangerSoft"
            size="icon"
            title={at("broadcast_button_remove", {}, "Remove button")}
            aria-label={at("broadcast_button_remove", {}, "Remove button")}
            onclick={() => removeButton(index)}
          >
            <Trash2 size={14} />
          </AdminButton>
        </div>
      {/each}
    </div>

    {#if result}
      <p class={result.kind === "ok" ? "admin-success" : "admin-error"} role="status">
        {result.message}
      </p>
    {/if}

    <div class="admin-message-actions">
      <AdminButton
        variant="primary"
        data-admin-action="send-user-message"
        disabled={!canSend}
        onclick={() => void send()}
      >
        <Send size={14} />
        {busy ? at("user_message_sending", {}, "Sending…") : at("btn_send_msg", {}, "Send message")}
      </AdminButton>
    </div>
  </div>
</section>

<style>
  .admin-user-message-channels {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: center;
  }

  .admin-user-message-channels .is-disabled {
    opacity: 0.6;
  }

  .admin-user-message-channels small {
    font-size: 11px;
  }

  .admin-user-message-buttons {
    display: grid;
    gap: 8px;
  }

  .admin-user-message-buttons-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 12px;
    color: var(--admin-muted);
  }

  .admin-user-message-button {
    display: grid;
    grid-template-columns: 170px minmax(0, 1fr) minmax(0, 1fr) auto;
    gap: 8px;
    align-items: center;
  }

  .admin-user-message-button :global(.input),
  .admin-user-message-button :global(.admin-select-trigger) {
    height: 36px;
    min-height: 36px;
  }

  @media (max-width: 720px) {
    .admin-user-message-button {
      grid-template-columns: minmax(0, 1fr) auto;
    }
  }
</style>

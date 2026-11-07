<script lang="ts">
  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

  let {
    title,
    body,
    clickTarget,
    titleLength,
    bodyLength,
    titleTruncated,
    bodyTruncated,
    hasImage,
    hasUndeliveredButtons,
    at,
  }: {
    title: string;
    body: string;
    clickTarget: string;
    titleLength: number;
    bodyLength: number;
    titleTruncated: boolean;
    bodyTruncated: boolean;
    hasImage: boolean;
    hasUndeliveredButtons: boolean;
    at: TranslateFn;
  } = $props();
</script>

<section
  class="broadcast-push-preview"
  aria-label={at("broadcast_push_preview_title", {}, "Push notification preview")}
>
  <div class="broadcast-push-device">
    <div class="broadcast-push-device-head">
      <span class="broadcast-push-app">{at("broadcast_push_app_name", {}, "Minishop")}</span>
      <span class="broadcast-push-now">{at("broadcast_push_now", {}, "now")}</span>
    </div>
    {#if title}
      <strong class="broadcast-push-title">{title}</strong>
    {/if}
    {#if body}
      <p class="broadcast-push-body">{body}</p>
    {:else if !title}
      <p class="broadcast-push-body broadcast-push-empty">
        {at("broadcast_push_preview_empty", {}, "Enter text to preview the push notification")}
      </p>
    {/if}
  </div>

  <div class="broadcast-push-meta">
    <span>
      {at(
        "broadcast_push_title_count",
        { count: titleLength, max: 120 },
        `Title ${titleLength}/120`
      )}
    </span>
    <span>
      {at("broadcast_push_body_count", { count: bodyLength, max: 200 }, `Body ${bodyLength}/200`)}
    </span>
    <span>
      {at("broadcast_push_click_target", { url: clickTarget }, `Tap opens ${clickTarget}`)}
    </span>
  </div>

  {#if titleTruncated || bodyTruncated}
    <small class="broadcast-push-warn">
      {at(
        "broadcast_push_truncated_hint",
        { title: 120, body: 200 },
        "Web Push shortens the text to the title and body limits above."
      )}
    </small>
  {/if}
  <small class="broadcast-push-hint">
    {at(
      "broadcast_push_plaintext_hint",
      {},
      "Push is plain text: Telegram HTML formatting is removed."
    )}
  </small>
  <small class="broadcast-push-hint">
    {at(
      "broadcast_push_buttons_hint",
      {},
      "Only the first link button becomes the tap target; other button types are not delivered to push."
    )}
  </small>
  {#if hasUndeliveredButtons}
    <small class="broadcast-push-hint">
      {at(
        "broadcast_push_buttons_ignored",
        {},
        "Additional buttons stay available to Telegram and email but are skipped for push."
      )}
    </small>
  {/if}
  {#if hasImage}
    <small class="broadcast-push-hint">
      {at("broadcast_push_image_hint", {}, "Images are not delivered to push.")}
    </small>
  {/if}
  <small class="broadcast-push-hint">
    {at(
      "broadcast_push_counts_hint",
      {},
      "The campaign counts each subscribed device that accepted the push; it does not confirm that the OS displayed it."
    )}
  </small>
</section>

<style>
  .broadcast-push-preview {
    display: grid;
    gap: 7px;
    padding: 12px 14px;
    border: 1px solid var(--admin-border, #2a2f3a);
    border-radius: 10px;
    background: var(--admin-surface-2, #10141b);
  }

  .broadcast-push-device {
    display: grid;
    gap: 4px;
    max-width: 360px;
    padding: 10px 12px;
    border-radius: 14px;
    background: color-mix(in srgb, var(--admin-surface, #0b0e14) 86%, white 14%);
    box-shadow: 0 8px 22px color-mix(in srgb, #000 24%, transparent);
  }

  .broadcast-push-device-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    color: var(--admin-text-dim, #5d6573);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.02em;
  }

  .broadcast-push-title {
    color: var(--admin-text, #e8edf5);
    font-size: 14px;
    line-height: 1.35;
  }

  .broadcast-push-body {
    margin: 0;
    color: var(--admin-text-muted, #9aa3b2);
    font-size: 13px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .broadcast-push-empty {
    color: var(--admin-text-dim, #5d6573);
  }

  .broadcast-push-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    color: var(--admin-text-muted, #9aa3b2);
    font-size: 11px;
  }

  .broadcast-push-warn {
    color: #f4b740;
    line-height: 1.35;
  }

  .broadcast-push-hint {
    color: var(--admin-text-muted, #9aa3b2);
    font-size: 11px;
    line-height: 1.35;
  }
</style>

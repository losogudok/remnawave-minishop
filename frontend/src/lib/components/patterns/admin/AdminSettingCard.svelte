<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";

  type Props = {
    title?: string;
    description?: string;
    class?: string;
    controlClass?: string;
    alignStart?: boolean;
    meta?: Snippet;
    children?: Snippet;
    footer?: Snippet;
  };

  let {
    title = "",
    description = "",
    class: className = "",
    controlClass = "",
    alignStart = false,
    meta,
    children,
    footer,
  }: Props = $props();
</script>

<div class={cn("admin-setting", "admin-setting-card", alignStart && "is-start", className)}>
  <div class="admin-setting-meta">
    {#if meta}
      {@render meta()}
    {:else}
      {#if title}<strong>{title}</strong>{/if}
      {#if description}<small>{description}</small>{/if}
    {/if}
  </div>
  <div class={cn("admin-setting-control", controlClass)}>
    {@render children?.()}
  </div>
  {#if footer}
    <div class="admin-setting-card-footer">
      {@render footer()}
    </div>
  {/if}
</div>

<style>
  .admin-setting.admin-setting-card {
    padding: 12px 14px;
    border: 1px solid color-mix(in srgb, var(--admin-border) 88%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--admin-surface-2) 78%, var(--admin-bg));
    transition:
      border-color 0.16s ease,
      background 0.16s ease;
  }

  .admin-setting-card:hover {
    border-color: var(--admin-border-strong);
    background: color-mix(in srgb, var(--admin-surface-2) 90%, var(--admin-bg));
  }

  .admin-setting-card.is-start {
    align-items: start;
  }

  .admin-setting-card :global(.admin-setting-control > .input),
  .admin-setting-card :global(.admin-setting-control > .admin-select-trigger) {
    width: 100%;
    height: 36px;
    min-height: 36px;
  }

  .admin-setting-card-footer {
    grid-column: 1 / -1;
    min-width: 0;
  }

  @media (max-width: 720px) {
    .admin-setting.admin-setting-card {
      gap: 10px;
      padding: 12px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .admin-setting.admin-setting-card {
      transition: none;
    }
  }
</style>

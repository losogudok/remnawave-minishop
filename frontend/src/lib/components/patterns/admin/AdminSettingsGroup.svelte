<script lang="ts">
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";

  type Props = {
    title: string;
    description?: string;
    class?: string;
    actions?: Snippet;
    children?: Snippet;
  };

  let { title, description = "", class: className = "", actions, children }: Props = $props();
</script>

<section class={cn("admin-settings-group", className)}>
  <header class="admin-settings-group-head">
    <div class="admin-settings-group-copy">
      <strong>{title}</strong>
      {#if description}<small>{description}</small>{/if}
    </div>
    {#if actions}
      <div class="admin-settings-group-actions">
        {@render actions()}
      </div>
    {/if}
  </header>
  <div class="admin-settings-group-body">
    {@render children?.()}
  </div>
</section>

<style>
  .admin-settings-group {
    display: grid;
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--admin-border);
    border-radius: 12px;
    background: color-mix(in srgb, var(--admin-surface-2) 58%, transparent);
  }

  .admin-settings-group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 11px 14px;
    border-bottom: 1px solid var(--admin-border);
    background: color-mix(in srgb, var(--admin-surface-2) 82%, transparent);
  }

  .admin-settings-group-copy {
    display: grid;
    gap: 3px;
    min-width: 0;
  }

  .admin-settings-group-copy strong {
    color: var(--admin-text);
    font-size: 13px;
    font-weight: 700;
    line-height: 1.3;
  }

  .admin-settings-group-copy small {
    color: var(--admin-muted);
    font-size: 12px;
    font-weight: 400;
    line-height: 1.4;
  }

  .admin-settings-group-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 0 0 auto;
  }

  .admin-settings-group-body {
    display: grid;
    gap: 8px;
    min-width: 0;
    padding: 10px;
  }

  @media (max-width: 720px) {
    .admin-settings-group-head {
      align-items: stretch;
      flex-direction: column;
      padding: 10px 12px;
    }

    .admin-settings-group-actions,
    .admin-settings-group-actions :global(.admin-btn) {
      width: 100%;
    }

    .admin-settings-group-body {
      padding: 8px;
    }
  }
</style>

<script lang="ts">
  import { Spinner } from "$components/ui/index.js";

  /**
   * Placeholder for a screen whose code is still downloading.
   *
   * Screens are fetched on first open, so this is what the customer sees for
   * the fraction of a second between tapping a tab and its chunk arriving —
   * and for longer on a bad connection, which is why it says what it is doing.
   */
  let { label = "" }: { label?: string } = $props();
</script>

<main class="content with-nav screen-loading" role="status" aria-live="polite">
  <Spinner size="lg" />
  {#if label}<span>{label}</span>{/if}
</main>

<style>
  .screen-loading {
    display: flex;
    flex-direction: column;
    gap: 12px;
    align-items: center;
    justify-content: center;
    min-height: 40vh;
    color: var(--muted);
    font-size: 13px;
  }
</style>

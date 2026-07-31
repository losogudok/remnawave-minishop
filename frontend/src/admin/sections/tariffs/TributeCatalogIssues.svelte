<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import {
    checkPeriodRows,
    checkTrafficRows,
    type TributeDraftRow,
    type TributeIssue,
  } from "$lib/admin/tributeCatalog";
  import { defaultCurrencyCode as getDefaultCurrencyCode } from "./tariffEditorTabUtils.js";
  import { tributeIssueText } from "./tributeIssues.js";
  import type { TranslateFn } from "./tariffEditorTabUtils.js";

  // Everything the loaded catalog disagrees with, for one set of draft rows.
  // ``extra`` carries what only the last binding pass could know — an unbound
  // row cannot be told apart from one deliberately kept off Tribute.
  let {
    at,
    rows,
    mode,
    extra = [],
  }: {
    at: TranslateFn;
    rows: TributeDraftRow[];
    mode: "period" | "product";
    extra?: TributeIssue[];
  } = $props();

  const tariffsStore = getTariffsStore();
  const catalog = $derived(tariffsStore.tributeCatalog);
  const error = $derived(tariffsStore.tributeCatalogError);
  const currency = $derived(getDefaultCurrencyCode(tariffsStore.tariffsCatalog));
  const issues = $derived(
    !catalog
      ? []
      : [
          ...extra,
          ...(mode === "period"
            ? checkPeriodRows(catalog, rows, currency)
            : checkTrafficRows(catalog, rows, currency)),
        ]
  );
</script>

{#if error}
  <p class="admin-muted">{error}</p>
{:else if catalog}
  {#if issues.length}
    <ul class="admin-tribute-issues">
      {#each issues as issue, index (index)}
        <li class="admin-muted">{tributeIssueText(at, issue)}</li>
      {/each}
    </ul>
  {:else}
    <p class="admin-muted">
      {at("tariff_tribute_check_ok", {}, "Checked against Tribute: no differences found")}
    </p>
  {/if}
{/if}

<style>
  /* Divergences read as a checklist, not as prose. */
  .admin-tribute-issues {
    margin: 0;
    padding-left: 1.1rem;
    display: grid;
    gap: 2px;
  }
</style>

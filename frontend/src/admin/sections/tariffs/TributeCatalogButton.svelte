<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { AdminButton } from "$components/patterns/admin/index.js";
  import { RefreshCw } from "$components/ui/icons.js";
  import type { TranslateFn } from "./tariffEditorTabUtils.js";

  // Reading the Creator catalog leaves the deployment for Tribute's API, so it
  // stays an explicit admin action rather than an on-open request.
  let { at }: { at: TranslateFn } = $props();

  const tariffsStore = getTariffsStore();
  const loading = $derived(tariffsStore.tributeCatalogLoading);
</script>

<AdminButton size="sm" onclick={() => tariffsStore.loadTributeCatalog()} disabled={loading}>
  <RefreshCw size={13} />
  {loading
    ? at("tariff_tribute_fetch_loading", {}, "Loading…")
    : at("tariff_tribute_fetch", {}, "Fetch from Tribute")}
</AdminButton>

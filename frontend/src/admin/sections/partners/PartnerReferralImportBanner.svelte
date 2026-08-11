<script lang="ts">
  import { onMount } from "svelte";
  import { CheckCircle2, RefreshCw, TriangleAlert, UserPlus } from "$components/ui/icons.js";
  import Dialog from "$components/ui/dialog.svelte";
  import { AdminBadge, AdminButton } from "$components/patterns/admin/index.js";
  import type { AdminApi } from "../../adminStores.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type ImportPreview = {
    partners: number;
    found: number;
    importable: number;
    existing: number;
    conflicts: number;
    historicalPayments: number;
  };
  type ImportResult = {
    partnersUpdated: number;
    imported: number;
    existing: number;
    conflicts: number;
  } | null;

  let {
    at,
    api,
    previewMode,
    onImported,
  }: {
    at: TranslateFn;
    api: AdminApi;
    previewMode: boolean;
    onImported: () => Promise<void>;
  } = $props();

  let preview = $state<ImportPreview>({
    partners: 0,
    found: 0,
    importable: 0,
    existing: 0,
    conflicts: 0,
    historicalPayments: 0,
  });
  let result = $state<ImportResult>(null);
  let loading = $state(true);
  let busy = $state(false);
  let error = $state("");
  let confirmOpen = $state(false);

  type RequestOptions = {
    method?: string;
    headers?: Record<string, string>;
    body?: string;
  };

  async function request(
    path: string,
    options: RequestOptions = {}
  ): Promise<Record<string, unknown>> {
    const call = api as unknown as (
      path: string,
      options?: RequestOptions
    ) => Promise<Record<string, unknown>>;
    return call(path, options);
  }

  async function loadPreview(): Promise<void> {
    loading = true;
    error = "";
    try {
      if (previewMode) {
        preview = {
          partners: 4,
          found: 18,
          importable: result ? 0 : 12,
          existing: result ? 15 : 3,
          conflicts: 3,
          historicalPayments: 9,
        };
      } else {
        const response = await request("/admin/partners/referral-import");
        const value = (response.preview || {}) as Record<string, unknown>;
        preview = {
          partners: Number(value.partners || 0),
          found: Number(value.found || 0),
          importable: Number(value.importable || 0),
          existing: Number(value.already_this_partner || 0),
          conflicts: Number(value.other_partner || 0) + Number(value.self_conflict || 0),
          historicalPayments: Number(value.historical_payments || 0),
        };
      }
    } catch (reason) {
      error =
        reason instanceof Error
          ? reason.message
          : at("partners_bulk_import_failed", {}, "Could not count referrals");
    } finally {
      loading = false;
    }
  }

  async function confirmImport(): Promise<void> {
    if (busy || !preview.importable) return;
    busy = true;
    error = "";
    try {
      if (previewMode) {
        result = {
          partnersUpdated: preview.partners,
          imported: preview.importable,
          existing: preview.existing,
          conflicts: preview.conflicts,
        };
      } else {
        const response = await request("/admin/partners/referral-import", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm_without_retroactive_commission: true }),
        });
        const value = (response.result || {}) as Record<string, unknown>;
        result = {
          partnersUpdated: Number(value.partners_updated || 0),
          imported: Number(value.imported || 0),
          existing: Number(value.existing || 0),
          conflicts: Number(value.conflicts || 0),
        };
        await onImported();
      }
      await loadPreview();
      confirmOpen = false;
    } catch (reason) {
      error =
        reason instanceof Error
          ? reason.message
          : at("partners_bulk_import_failed", {}, "Could not convert referrals");
    } finally {
      busy = false;
    }
  }

  onMount(() => void loadPreview());
</script>

<section class="partners-referral-import-banner" aria-labelledby="partners-referral-import-title">
  <span class="partners-referral-import-icon"><UserPlus size={22} /></span>
  <div class="partners-referral-import-content">
    <header>
      <h2 id="partners-referral-import-title">
        {at("partners_bulk_import_title", {}, "Convert referrals to partner clients")}
      </h2>
      <AdminBadge variant="warning">
        {at("partners_bulk_import_disabled_badge", {}, "Referral system disabled")}
      </AdminBadge>
    </header>
    <p>
      {at(
        "partners_bulk_import_description",
        {},
        "Keep existing referral relationships by assigning users to their partners as clients."
      )}
    </p>

    {#if loading}
      <div class="partners-referral-import-loading" role="status">
        <RefreshCw size={16} />{at("partners_bulk_import_loading", {}, "Counting referrals…")}
      </div>
    {:else if error}
      <div class="partners-referral-import-error" role="alert">
        <TriangleAlert size={16} /><span>{error}</span>
        <button type="button" onclick={() => void loadPreview()}>
          {at("partners_retry", {}, "Try again")}
        </button>
      </div>
    {:else}
      <div class="partners-referral-import-stats">
        <span>
          <strong>{preview.importable}</strong>
          {at("partners_bulk_import_available", {}, "users to convert")}
        </span>
        <span>
          <strong>{preview.partners}</strong>
          {at("partners_bulk_import_partners", {}, "partners with referrals")}
        </span>
        <span>
          <strong>{preview.existing}</strong>
          {at("partners_bulk_import_existing", {}, "already clients")}
        </span>
        <span>
          <strong>{preview.conflicts}</strong>
          {at("partners_bulk_import_conflicts", {}, "conflicts")}
        </span>
      </div>

      {#if result}
        <div class="partners-referral-import-result" role="status">
          <CheckCircle2 size={17} />
          <span>
            {at(
              "partners_bulk_import_success",
              { count: result.imported, partners: result.partnersUpdated },
              `Converted ${result.imported} users for ${result.partnersUpdated} partners.`
            )}
          </span>
        </div>
      {/if}
    {/if}
  </div>
  <div class="partners-referral-import-actions">
    {#if !loading && !error && preview.importable > 0}
      <AdminButton variant="primary" onclick={() => (confirmOpen = true)}>
        {at("partners_bulk_import_action", {}, "Convert referrals")}
      </AdminButton>
    {:else if !loading && !error}
      <span class="partners-referral-import-complete">
        <CheckCircle2 size={16} />{at(
          "partners_bulk_import_nothing",
          {},
          "All available referrals are already clients"
        )}
      </span>
    {/if}
  </div>
</section>

<Dialog
  open={confirmOpen}
  title={at("partners_bulk_import_confirm_title", {}, "Convert all available referrals?")}
  description={at(
    "partners_bulk_import_confirm_description",
    { count: preview.importable },
    `${preview.importable} users will become partner clients.`
  )}
  closeLabel={at("close", {}, "Close")}
  onclose={() => (confirmOpen = false)}
  class="admin-dialog admin-dialog-compact admin-partners-dialog"
>
  <div class="admin-form partners-dialog-form" data-dialog-content>
    <div class="partners-import-preview">
      <span>
        <strong>{preview.importable}</strong>
        {at("partners_bulk_import_available", {}, "users to convert")}
      </span>
      <span>
        <strong>{preview.partners}</strong>
        {at("partners_bulk_import_partners", {}, "partners with referrals")}
      </span>
      <span>
        <strong>{preview.existing}</strong>
        {at("partners_bulk_import_existing", {}, "already clients")}
      </span>
      <span>
        <strong>{preview.conflicts}</strong>
        {at("partners_bulk_import_conflicts", {}, "conflicts")}
      </span>
    </div>
    <div class="partners-warning">
      <TriangleAlert size={17} />
      <span>
        {at(
          "partners_bulk_import_no_retro",
          { count: preview.historicalPayments },
          `${preview.historicalPayments} historical payments will not be recalculated. Eligibility starts now.`
        )}
      </span>
    </div>
    {#if error}
      <div class="partners-referral-import-error" role="alert">
        <TriangleAlert size={16} /><span>{error}</span>
      </div>
    {/if}
    <div class="admin-dialog-actions">
      <AdminButton onclick={() => (confirmOpen = false)} disabled={busy}>
        {at("cancel", {}, "Cancel")}
      </AdminButton>
      <AdminButton variant="primary" onclick={confirmImport} disabled={busy}>
        {busy
          ? at("partners_bulk_import_converting", {}, "Converting…")
          : at("confirm", {}, "Confirm")}
      </AdminButton>
    </div>
  </div>
</Dialog>

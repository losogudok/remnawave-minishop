<script lang="ts">
  import Dialog from "$components/ui/dialog.svelte";
  import { TriangleAlert } from "$components/ui/icons.js";
  import { AdminButton } from "$components/patterns/admin/index.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

  let {
    at,
    pendingToggle,
    autoEnrollmentConfirmation,
    oncloseToggle,
    onconfirmToggle,
    oncloseAutoEnrollment,
    onconfirmAutoEnrollment,
  }: {
    at: TranslateFn;
    pendingToggle: string;
    autoEnrollmentConfirmation: boolean;
    oncloseToggle: () => void;
    onconfirmToggle: () => void;
    oncloseAutoEnrollment: () => void;
    onconfirmAutoEnrollment: () => void;
  } = $props();

  const pendingToggleLocaleKey = $derived(
    pendingToggle === "withdrawalsEnabled"
      ? "withdrawals"
      : pendingToggle === "balancePaymentEnabled"
        ? "balance_payment"
        : "program"
  );
</script>

<Dialog
  open={Boolean(pendingToggle)}
  title={at(
    `partner_settings_confirm_${pendingToggleLocaleKey}_title`,
    {},
    "Disable this feature?"
  )}
  closeLabel={at("close", {}, "Close")}
  onclose={oncloseToggle}
  class="admin-dialog admin-dialog-compact admin-partner-settings-dialog"
>
  {#snippet titleIcon()}<TriangleAlert size={22} />{/snippet}
  <div class="admin-form" data-dialog-content>
    <p class="partner-settings-confirm-text">
      {at(
        `partner_settings_confirm_${pendingToggleLocaleKey}_text`,
        {},
        "Existing history and obligations remain available. Only new user actions are stopped."
      )}
    </p>
    <div class="admin-dialog-actions">
      <AdminButton onclick={oncloseToggle}>{at("cancel", {}, "Cancel")}</AdminButton>
      <AdminButton variant="danger" onclick={onconfirmToggle}>
        {at("confirm", {}, "Confirm")}
      </AdminButton>
    </div>
  </div>
</Dialog>

<Dialog
  open={autoEnrollmentConfirmation}
  title={at("partner_settings_confirm_auto_enrollment_title", {}, "Enable automatic enrollment?")}
  closeLabel={at("close", {}, "Close")}
  onclose={oncloseAutoEnrollment}
  class="admin-dialog admin-dialog-compact admin-partner-settings-dialog"
>
  {#snippet titleIcon()}<TriangleAlert size={22} />{/snippet}
  <div class="admin-form" data-dialog-content>
    <p class="partner-settings-confirm-text">
      {at(
        "partner_settings_confirm_auto_enrollment_text",
        {},
        "Saving will enable the program and create active partner profiles for every eligible user. This does not reactivate paused or closed profiles."
      )}
    </p>
    <div class="admin-dialog-actions">
      <AdminButton onclick={oncloseAutoEnrollment}>{at("cancel", {}, "Cancel")}</AdminButton>
      <AdminButton onclick={onconfirmAutoEnrollment}>{at("confirm", {}, "Confirm")}</AdminButton>
    </div>
  </div>
</Dialog>

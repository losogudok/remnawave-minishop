<script
  lang="ts"
  generics="Row extends { id: number; kind: string; label: string; url: string; promoCode: string }"
>
  import { AdminButton, AdminSelect } from "$components/patterns/admin/index.js";
  import { Input, Sortable } from "$components/ui/index.js";
  import { Plus, Trash2 } from "$components/ui/icons.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type SelectOption = { value: string; label: string; disabled?: boolean; group?: string };

  let {
    buttons,
    at,
    max = 4,
    promoOptions = [],
    promoOptionsLoading = false,
    promoOptionsLoaded = false,
    extraKinds = [],
    onAdd,
    onRemove,
    onUpdate,
    onReorder,
    onRequestPromoOptions,
  }: {
    buttons: Row[];
    at: TranslateFn;
    max?: number;
    promoOptions?: SelectOption[];
    promoOptionsLoading?: boolean;
    promoOptionsLoaded?: boolean;
    /** Extra button kinds a host offers on top of the shared ones. */
    extraKinds?: SelectOption[];
    onAdd: () => void;
    onRemove: (index: number) => void;
    onUpdate: (index: number, fields: Partial<Row>) => void;
    onReorder: (from: number, to: number) => void;
    onRequestPromoOptions?: () => void;
  } = $props();

  const kindOptions = $derived([
    { value: "url", label: at("broadcast_button_kind_url", {}, "Link") },
    {
      value: "promo_bot",
      label: at("broadcast_button_kind_promo_bot", {}, "Promo code — in bot"),
    },
    {
      value: "promo_webapp",
      label: at("broadcast_button_kind_promo_webapp", {}, "Promo code — in web app"),
    },
    ...extraKinds,
  ]);
  // A kind outside the shared three is host-owned and carries no promo code.
  const sharedKinds = new Set(["url", "promo_bot", "promo_webapp"]);
  const hasPromoButtons = $derived(
    buttons.some((button) => button.kind !== "url" && sharedKinds.has(button.kind))
  );

  // Codes are only worth fetching once a promo button actually exists.
  $effect(() => {
    if (hasPromoButtons) onRequestPromoOptions?.();
  });

  function needsPromoCode(kind: string): boolean {
    return kind !== "url" && sharedKinds.has(kind);
  }
</script>

<div class="admin-row-editor">
  <Sortable
    items={buttons}
    class="admin-row-editor-line admin-row-editor-broadcast"
    getKey={(button: Row) => button.id}
    handleLabel={at("broadcast_button_reorder", {}, "Drag to reorder buttons")}
    {onReorder}
  >
    {#snippet children(button: Row, index: number)}
      <AdminSelect
        value={button.kind}
        items={kindOptions}
        ariaLabel={at("broadcast_buttons_label", {}, "Buttons")}
        onValueChange={(value) => onUpdate(index, { kind: value } as Partial<Row>)}
      />
      <Input
        class="input"
        value={button.label}
        maxlength={64}
        placeholder={at("broadcast_button_label_placeholder", {}, "Button label")}
        oninput={(event) =>
          onUpdate(index, {
            label: (event.currentTarget as HTMLInputElement).value,
          } as Partial<Row>)}
      />
      {#if needsPromoCode(button.kind)}
        <AdminSelect
          value={button.promoCode}
          items={promoOptions}
          placeholder={promoOptionsLoading
            ? at("broadcast_button_promo_loading", {}, "Loading codes...")
            : at("broadcast_button_promo_select", {}, "Select a code")}
          ariaLabel={at("broadcast_button_promo_select", {}, "Select a code")}
          onValueChange={(value) => onUpdate(index, { promoCode: value } as Partial<Row>)}
        />
      {:else if button.kind === "url"}
        <Input
          class="input"
          value={button.url}
          placeholder={at("broadcast_button_url_placeholder", {}, "https://…")}
          oninput={(event) =>
            onUpdate(index, {
              url: (event.currentTarget as HTMLInputElement).value,
            } as Partial<Row>)}
        />
      {:else}
        <span class="admin-muted admin-row-editor-static">
          {at("broadcast_button_no_target", {}, "Target is chosen automatically")}
        </span>
      {/if}
      <AdminButton
        size="sm"
        variant="danger"
        aria-label={at("broadcast_button_remove", {}, "Remove button")}
        onclick={() => onRemove(index)}
      >
        <Trash2 size={13} />
      </AdminButton>
    {/snippet}
  </Sortable>
</div>

{#if hasPromoButtons && promoOptionsLoaded && !promoOptions.length}
  <small class="admin-muted">
    {at(
      "broadcast_no_promos_hint",
      {},
      "No active codes — create one in the Promo codes section first"
    )}
  </small>
{/if}

{#if buttons.length < max}
  <div>
    <AdminButton variant="ghost" onclick={onAdd}>
      <Plus size={14} />
      {at("broadcast_button_add", {}, "Add button")}
    </AdminButton>
  </div>
{/if}

<style>
  .admin-row-editor-static {
    font-size: 12px;
    align-self: center;
  }
</style>

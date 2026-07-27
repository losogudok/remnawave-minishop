<script lang="ts">
  import { getTariffsStore } from "$lib/admin/context";
  import { Input } from "$components/ui/index.js";
  import { AdminSelect } from "$components/patterns/admin/index.js";
  import { applyProductToTrafficRow, productOptionLabel } from "$lib/admin/tributeCatalog";
  import { draftRowInputHandler, type DraftRow, type TranslateFn } from "./tariffEditorTabUtils.js";
  import type { DraftRowsField } from "$lib/admin/stores/tariffsStore";

  // Before the catalog is loaded the raw id stays editable; afterwards the same
  // cell becomes a picker that also fills the product link Tribute publishes.
  let {
    at,
    field,
    index,
    row,
  }: { at: TranslateFn; field: DraftRowsField; index: number; row: DraftRow } = $props();

  const tariffsStore = getTariffsStore();
  const products = $derived(tariffsStore.tributeCatalog?.products || []);
  const currentId = $derived(String(row.tribute_product_id ?? ""));
  const options = $derived(
    products.map((product) => ({
      value: String(product.product_id),
      label: productOptionLabel(product),
    }))
  );
  const items = $derived(
    !currentId || options.some((option) => option.value === currentId)
      ? options
      : [
          {
            value: currentId,
            label: at(
              "tariff_tribute_product_unknown",
              { id: currentId },
              "#{id} (not in Tribute)"
            ),
          },
          ...options,
        ]
  );

  function applyProduct(value: string): void {
    const product = products.find((item) => String(item.product_id) === value);
    if (!product) return;
    const result = applyProductToTrafficRow(product, row, "");
    tariffsStore.updateDraftRow(field, index, result.values);
  }
</script>

{#if options.length}
  <AdminSelect
    value={currentId}
    {items}
    placeholder={at("tariff_tribute_pick_placeholder_product", {}, "Select a product")}
    ariaLabel={at("tariff_label_tribute_product_id", {}, "Tribute product ID")}
    onValueChange={applyProduct}
  />
{:else}
  <Input
    class="input"
    type="number"
    min="1"
    step="1"
    placeholder={at("tariff_placeholder_tribute_product_id", {}, "e.g. 501")}
    value={row.tribute_product_id}
    oninput={draftRowInputHandler(tariffsStore, field, index, "tribute_product_id")}
    aria-label={at("tariff_label_tribute_product_id", {}, "Tribute product ID")}
  />
{/if}

<script lang="ts">
  type Item = { value: string; label: string };

  let {
    value,
    items,
    ariaLabel = "",
    variant = "period",
    onValueChange = () => {},
  }: {
    value: string;
    items: Item[];
    ariaLabel?: string;
    variant?: "period" | "granularity";
    onValueChange?: (value: string) => void;
  } = $props();
</script>

<div
  class:admin-revenue-period={variant === "period"}
  class:admin-revenue-granularity={variant === "granularity"}
  role="tablist"
  aria-label={ariaLabel}
>
  {#each items as item (item.value)}
    <button
      type="button"
      class="admin-revenue-period-btn"
      class:admin-revenue-period-btn--compact={variant === "granularity"}
      class:is-active={value === item.value}
      role="tab"
      aria-selected={value === item.value}
      onclick={() => onValueChange(item.value)}
    >
      {item.label}
    </button>
  {/each}
</div>

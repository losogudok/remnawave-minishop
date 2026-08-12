<script lang="ts">
  /**
   * The action row at the foot of a card or panel.
   *
   * Before this existed every panel hand-rolled its own footer, so some had a
   * divider and some did not, and one (the partner decision panel) had no rule
   * at all and pinned its buttons to the card's edge. Anything with buttons
   * under a card body should use this instead of a bare `<footer>`.
   */
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  type Props = Omit<HTMLAttributes<HTMLElement>, "class" | "children"> & {
    children?: Snippet;
    class?: string;
    /** `end` right-aligns; `between` pushes the first item to the left edge. */
    align?: "end" | "between";
    /** Drop the divider when the card body already ends in one. */
    divider?: boolean;
  };

  let { children, class: className = "", align = "end", divider = true, ...rest }: Props = $props();
</script>

<footer
  class={cn(
    "admin-card-actions",
    align === "between" && "admin-card-actions-between",
    !divider && "admin-card-actions-flush",
    className
  )}
  {...rest}
>
  {@render children?.()}
</footer>

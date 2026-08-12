<script lang="ts">
  /**
   * A small floating panel with a notch pointing at whatever it explains.
   *
   * Shared by the chart readout and the partner tour so both speak the same
   * visual language: same surface, same border, same notch geometry. The plate
   * only draws itself — positioning stays with the caller, because a chart
   * readout slides along a lane while a coach mark is placed by floating-ui.
   *
   * Colours come from `--ui-plate-bg` / `--ui-plate-border`, so a caller can
   * tint the plate (the chart uses an accent-tinted border) without restating
   * the notch.
   */
  import { cn } from "$lib/utils.js";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  type Props = Omit<HTMLAttributes<HTMLDivElement>, "class" | "children"> & {
    children?: Snippet;
    class?: string;
    /** Edge the notch sits on. `top` means the plate hangs below its subject. */
    arrow?: "top" | "bottom" | "none";
    /** Position of the notch along that edge — any CSS length or percentage. */
    arrowX?: string;
    /** Drives the fade; the plate keeps its box so layout never jumps. */
    visible?: boolean;
    ref?: HTMLDivElement | null;
    style?: string;
  };

  let {
    arrow = "top",
    arrowX = "50%",
    visible = true,
    class: className = "",
    children,
    ref = $bindable(null),
    style = "",
    ...rest
  }: Props = $props();
</script>

<div
  bind:this={ref}
  {...rest}
  class={cn("ui-plate", arrow !== "none" && `ui-plate--arrow-${arrow}`, className)}
  class:is-visible={visible}
  style={`--ui-plate-arrow-x: ${arrowX}; ${style}`}
>
  {#if arrow !== "none"}
    <span class="ui-plate-arrow" aria-hidden="true"></span>
  {/if}
  {@render children?.()}
</div>

<style>
  /*
   * Everything a caller may need to vary goes through a custom property rather
   * than a plain declaration. Svelte scopes these rules with a hash class, so a
   * caller's own class would lose a specificity fight over `position` or
   * `border-radius`; a variable has no such fight to lose. For the same reason
   * the defaults are written as `var(--x, fallback)` at each use instead of
   * being declared here — declaring them would re-create the fight one level up
   * and silently beat the caller's own `--ui-plate-bg`.
   */
  .ui-plate {
    position: var(--ui-plate-position, relative);
    box-sizing: border-box;
    border: 1px solid var(--ui-plate-border, var(--border));
    border-radius: var(--ui-plate-radius, 12px);
    background: var(--ui-plate-bg, var(--surface-2, var(--surface)));
    opacity: 0;
    visibility: hidden;
    transition: var(--ui-plate-transition, opacity 0.14s ease, visibility 0s linear 0.14s);
  }

  /* Hiding uses the base rule's transition, showing uses this one, so a caller
     can leave quickly and come back gently. */
  .ui-plate.is-visible {
    opacity: 1;
    visibility: visible;
    transition: var(
      --ui-plate-transition-in,
      var(--ui-plate-transition, opacity 0.14s ease, visibility 0s linear 0.14s)
    );
    transition-delay: 0s;
  }

  /* The notch is a rotated square sharing the plate's border and fill, so it
     reads as part of the same surface instead of a pasted-on triangle. */
  .ui-plate-arrow {
    --ui-plate-arrow-size: var(--ui-plate-arrow, 10px);
    position: absolute;
    left: var(--ui-plate-arrow-x, 50%);
    width: var(--ui-plate-arrow-size);
    height: var(--ui-plate-arrow-size);
    border-top: 1px solid var(--ui-plate-border, var(--border));
    border-left: 1px solid var(--ui-plate-border, var(--border));
    background: var(--ui-plate-bg, var(--surface-2, var(--surface)));
  }

  .ui-plate--arrow-top > .ui-plate-arrow {
    top: calc(var(--ui-plate-arrow-size) / -2 - 1px);
    transform: translateX(-50%) rotate(45deg);
  }

  .ui-plate--arrow-bottom > .ui-plate-arrow {
    bottom: calc(var(--ui-plate-arrow-size) / -2 - 1px);
    transform: translateX(-50%) rotate(225deg);
  }
</style>

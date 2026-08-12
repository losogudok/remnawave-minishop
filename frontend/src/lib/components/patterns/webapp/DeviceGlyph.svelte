<script lang="ts">
  // Composite device icon: the hardware silhouette is stroked, the OS mark is
  // filled onto its screen. Decorative — the platform label sits next to it.
  import { deviceGlyphPaths } from "$lib/webapp/deviceGlyph.js";

  type Props = {
    device?: unknown;
    size?: number;
  };

  let { device = null, size = 24 }: Props = $props();

  const glyph = $derived(deviceGlyphPaths(device as Record<string, unknown> | null));
</script>

<svg
  class="device-glyph"
  data-device-shape={glyph.shape}
  data-device-os={glyph.os}
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.6"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#each glyph.outline as d (d)}
    <path {d} />
  {/each}
  {#if glyph.mark.length}
    <g transform={glyph.markTransform} fill="currentColor" fill-rule="evenodd" stroke="none">
      {#each glyph.mark as d (d)}
        <path {d} />
      {/each}
    </g>
  {/if}
</svg>

<script lang="ts">
  import { Activity } from "$components/ui/icons.js";

  let {
    label,
    plotHeight = 204,
  }: {
    label: string;
    plotHeight?: number;
  } = $props();

  const bodyHeight = $derived(plotHeight + 74);
</script>

<div class="admin-chart-empty" style={`min-height:${bodyHeight}px`} role="status">
  <Activity size={24} aria-hidden="true" />
  <strong>{label}</strong>
</div>

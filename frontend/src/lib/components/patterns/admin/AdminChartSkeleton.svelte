<script lang="ts">
  import Skeleton from "$components/ui/skeleton.svelte";

  let { plotHeight = 204 }: { plotHeight?: number } = $props();
</script>

<div class="admin-revenue-chart-body" aria-hidden="true">
  <Skeleton class="admin-revenue-chart-skeleton" width="100%" height={`${plotHeight}px`} />
  <div class="admin-chart-tooltip-lane admin-chart-tooltip-lane--skeleton">
    <div class="admin-chart-tooltip-skeleton">
      <Skeleton variant="tiny" width="42%" />
      <Skeleton variant="line" width="76%" height="13px" />
    </div>
  </div>
</div>

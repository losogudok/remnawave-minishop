<script lang="ts">
  import Card from "$components/ui/card.svelte";
  import Skeleton from "$components/ui/skeleton.svelte";

  let { label }: { label: string } = $props();
</script>

<div class="partner-skeleton" role="status" aria-label={label}>
  <Card class="partner-overview-card partner-skeleton-card">
    <div class="partner-overview-head">
      <div class="partner-overview-title">
        <Skeleton class="partner-skeleton-icon" variant="dot" width="34px" height="34px" />
        <div>
          <Skeleton variant="title" width="156px" />
          <Skeleton variant="badge" width="72px" />
        </div>
      </div>
      <Skeleton class="partner-skeleton-button" width="122px" height="34px" />
    </div>
    <div class="partner-skeleton-copy">
      <Skeleton variant="line" width="92%" />
      <Skeleton variant="tiny" width="64%" />
    </div>
    <div class="partner-links-section">
      <Skeleton variant="title" width="132px" />
      <div class="partner-link-list">
        {#each Array(2) as _, index (index)}
          <div class="partner-link-item">
            <Skeleton variant="tiny" width={index ? "104px" : "126px"} />
            <div class="copy-row referral-copy-row partner-copy-row">
              <Skeleton class="partner-skeleton-field" height="40px" />
              <Skeleton class="partner-skeleton-button" width="108px" height="40px" />
            </div>
          </div>
        {/each}
      </div>
    </div>
  </Card>

  <Card class="partner-balance-card partner-skeleton-card">
    <div class="partner-section-head">
      <div class="partner-section-title">
        <Skeleton class="partner-skeleton-icon" variant="dot" width="30px" height="30px" />
        <div>
          <Skeleton variant="title" width="142px" />
          <Skeleton variant="tiny" width="196px" />
        </div>
      </div>
    </div>
    <div class="partner-balance-summary">
      <div class="partner-balance-main">
        <Skeleton variant="tiny" width="112px" />
        <Skeleton variant="line" width="168px" height="34px" />
      </div>
      <Skeleton class="partner-skeleton-button" width="112px" height="40px" />
      <div class="partner-balance-breakdown">
        {#each [58, 72, 66] as width (width)}
          <div>
            <Skeleton variant="tiny" width={`${width}%`} />
            <Skeleton variant="line" width={`${Math.max(46, width - 8)}%`} height="13px" />
          </div>
        {/each}
      </div>
    </div>
  </Card>

  <Card class="partner-stats-card partner-skeleton-card">
    <div class="partner-section-head">
      <div class="partner-section-title">
        <Skeleton class="partner-skeleton-icon" variant="dot" width="30px" height="30px" />
        <div>
          <Skeleton variant="title" width="174px" />
          <Skeleton variant="tiny" width="226px" />
        </div>
      </div>
    </div>
    <section class="partner-kpis" aria-hidden="true">
      {#each [62, 48, 74, 58] as width (width)}
        <div>
          <Skeleton variant="tiny" width={`${width}%`} />
          <Skeleton variant="line" width={`${Math.max(42, width - 12)}%`} height="16px" />
        </div>
      {/each}
    </section>
    <div class="partner-activity">
      <div class="partner-skeleton-tabs">
        {#each [92, 106, 94] as width (width)}
          <Skeleton class="partner-skeleton-tab" width={`${width}px`} height="34px" />
        {/each}
      </div>
      <div class="partner-skeleton-table">
        <div class="partner-skeleton-table-head">
          {#each [72, 54, 62, 50, 58] as width (width)}
            <Skeleton variant="tiny" width={`${width}%`} />
          {/each}
        </div>
        {#each Array(4) as _, rowIndex (rowIndex)}
          <div class="partner-skeleton-table-row">
            {#each [78, 62, 54, 42, 56] as width, cellIndex (`${rowIndex}-${cellIndex}`)}
              <Skeleton variant="line" width={`${Math.max(34, width - rowIndex * 4)}%`} />
            {/each}
          </div>
        {/each}
      </div>
      <div class="partner-skeleton-pagination">
        <Skeleton variant="tiny" width="118px" />
        <Skeleton class="partner-skeleton-button" width="116px" height="30px" />
      </div>
    </div>
  </Card>
</div>

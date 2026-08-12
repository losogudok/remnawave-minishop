<script lang="ts">
  import { TrendingUp, WalletCards } from "$components/ui/icons.js";
  import {
    AdminChartEmptyState,
    AdminRevenueChart,
    AdminRevenueTabs,
  } from "$components/patterns/admin/index.js";
  import {
    aggregateRevenueSeries,
    filterRevenueByPreset,
    hasChartValues,
    isRevenueGranularity,
    isRevenuePreset,
    REVENUE_GRANULARITIES,
    REVENUE_PRESETS,
    type RevenueGranularity,
    type RevenuePreset,
  } from "$lib/admin/revenueSeriesAgg.js";
  import type { PartnerChartPoint } from "$lib/admin/previewMock/partnerProgram.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  let {
    at,
    currentLang = "en",
    currency,
    money,
    partnerRevenueDaily,
    partnerPayoutsDaily,
  }: {
    at: TranslateFn;
    currentLang?: string;
    currency: string;
    money: (value: number) => string;
    partnerRevenueDaily: PartnerChartPoint[];
    partnerPayoutsDaily: PartnerChartPoint[];
  } = $props();

  const PLOT_HEIGHT = 208;

  let range = $state<RevenuePreset>(30);
  let granularity = $state<RevenueGranularity>("day");
  const rangeEndIso = $derived(
    [...partnerRevenueDaily, ...partnerPayoutsDaily].reduce(
      (latest, point) => (point.date > latest ? point.date : latest),
      ""
    )
  );

  const revenueSeries = $derived(
    aggregateRevenueSeries(
      filterRevenueByPreset(partnerRevenueDaily, range, rangeEndIso),
      granularity
    )
  );
  const payoutSeries = $derived(
    aggregateRevenueSeries(
      filterRevenueByPreset(partnerPayoutsDaily, range, rangeEndIso),
      granularity
    )
  );
  const revenueTotal = $derived(revenueSeries.reduce((sum, point) => sum + point.amount, 0));
  const payoutTotal = $derived(payoutSeries.reduce((sum, point) => sum + point.amount, 0));
  const revenueHasValues = $derived(hasChartValues(revenueSeries));
  const payoutHasValues = $derived(hasChartValues(payoutSeries));
</script>

<!-- One card, laid out like the dashboard revenue chart: a shared head with the
     range and granularity tabs, then each series inside the same dark plot
     frame the dashboard uses. -->
<article class="admin-card admin-revenue-chart partners-chart-card">
  <div class="admin-revenue-chart-head">
    <div class="admin-revenue-chart-title">
      {at("partners_chart_title", {}, "Partner program dynamics")}
    </div>
    <div class="admin-revenue-chart-toolbar">
      <AdminRevenueTabs
        value={String(range)}
        items={REVENUE_PRESETS.map((preset) => ({
          value: String(preset),
          label: at(
            `stats_revenue_period_${preset}`,
            {},
            preset === "all" ? "All time" : `${preset}d`
          ),
        }))}
        ariaLabel={at("partners_range", {}, "Chart range")}
        onValueChange={(value) => {
          const next = value === "all" ? "all" : Number(value);
          if (isRevenuePreset(next)) range = next;
        }}
      />
    </div>
  </div>

  <AdminRevenueTabs
    value={granularity}
    items={REVENUE_GRANULARITIES.map((value) => ({
      value,
      label: at(`stats_revenue_granularity_${value}`, {}, value),
    }))}
    ariaLabel={at("partners_granularity", {}, "Chart granularity")}
    variant="granularity"
    onValueChange={(value) => {
      if (isRevenueGranularity(value)) granularity = value;
    }}
  />

  <div class="partners-chart-stack">
    <section class="partners-chart-block">
      <header>
        <span><TrendingUp size={16} /></span>
        <strong>{at("partners_chart_revenue", {}, "Revenue from partner clients")}</strong>
        <b>{money(revenueTotal)}</b>
      </header>
      <div class="admin-revenue-svg-frame admin-revenue-svg-frame--chart">
        {#if revenueHasValues}
          <AdminRevenueChart
            series={revenueSeries}
            plotHeight={PLOT_HEIGHT}
            fmtMoney={(value) => money(value)}
            {currency}
            locale={currentLang}
            {granularity}
            legendTimeLabel={at("partners_chart_time", {}, "Time")}
            legendValueLabel={at("partners_chart_revenue_value", {}, "Revenue")}
            legendDeltaLabel={at("partners_chart_delta", {}, "Change")}
          />
        {:else}
          <AdminChartEmptyState
            label={at("stats_revenue_no_chart", {}, "No data yet")}
            plotHeight={PLOT_HEIGHT}
          />
        {/if}
      </div>
    </section>

    <section class="partners-chart-block">
      <header>
        <span><WalletCards size={16} /></span>
        <strong>{at("partners_chart_payouts", {}, "Paid withdrawals")}</strong>
        <b>{money(payoutTotal)}</b>
      </header>
      <div class="admin-revenue-svg-frame admin-revenue-svg-frame--chart">
        {#if payoutHasValues}
          <AdminRevenueChart
            series={payoutSeries}
            plotHeight={PLOT_HEIGHT}
            fmtMoney={(value) => money(value)}
            {currency}
            locale={currentLang}
            {granularity}
            legendTimeLabel={at("partners_chart_time", {}, "Time")}
            legendValueLabel={at("partners_chart_payout_value", {}, "Withdrawals")}
            legendDeltaLabel={at("partners_chart_delta", {}, "Change")}
            variant="bar"
          />
        {:else}
          <AdminChartEmptyState
            label={at("stats_revenue_no_chart", {}, "No data yet")}
            plotHeight={PLOT_HEIGHT}
          />
        {/if}
      </div>
    </section>
  </div>
</article>

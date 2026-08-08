<script lang="ts">
  import { TrendingUp, WalletCards } from "$components/ui/icons.js";
  import { AdminRevenueChart, AdminRevenueTabs } from "$components/patterns/admin/index.js";
  import { aggregateRevenueSeries, sliceLastDays } from "$lib/admin/revenueSeriesAgg.js";
  import type { PartnerChartPoint } from "$lib/admin/previewMock/partnerProgram.js";

  type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
  type Granularity = "day" | "week" | "month";

  let {
    at,
    currency,
    money,
    partnerRevenueDaily,
    partnerPayoutsDaily,
  }: {
    at: TranslateFn;
    currency: string;
    money: (value: number) => string;
    partnerRevenueDaily: PartnerChartPoint[];
    partnerPayoutsDaily: PartnerChartPoint[];
  } = $props();

  const RANGES = [7, 14, 30, 90, 180, 365];
  const PLOT_HEIGHT = 208;

  let range = $state("30");
  let granularity = $state<Granularity>("day");

  const revenueSeries = $derived(
    aggregateRevenueSeries(sliceLastDays(partnerRevenueDaily, Number(range)), granularity)
  );
  const payoutSeries = $derived(
    aggregateRevenueSeries(sliceLastDays(partnerPayoutsDaily, Number(range)), granularity)
  );
  const revenueTotal = $derived(revenueSeries.reduce((sum, point) => sum + point.amount, 0));
  const payoutTotal = $derived(payoutSeries.reduce((sum, point) => sum + point.amount, 0));
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
        value={range}
        items={RANGES.map((days) => ({
          value: String(days),
          label: at(`stats_revenue_period_${days}`, {}, `${days}d`),
        }))}
        ariaLabel={at("partners_range", {}, "Chart range")}
        onValueChange={(value) => (range = value)}
      />
    </div>
  </div>

  <AdminRevenueTabs
    value={granularity}
    items={["day", "week", "month"].map((value) => ({
      value,
      label: at(`stats_revenue_granularity_${value}`, {}, value),
    }))}
    ariaLabel={at("partners_granularity", {}, "Chart granularity")}
    variant="granularity"
    onValueChange={(value) => {
      if (value === "day" || value === "week" || value === "month") granularity = value;
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
        <AdminRevenueChart
          series={revenueSeries}
          plotHeight={PLOT_HEIGHT}
          fmtMoney={(value) => money(value)}
          {currency}
          legendTimeLabel={at("partners_chart_time", {}, "Time")}
          legendValueLabel={at("partners_chart_revenue_value", {}, "Revenue")}
          legendDeltaLabel={at("partners_chart_delta", {}, "Change")}
        />
      </div>
    </section>

    <section class="partners-chart-block">
      <header>
        <span><WalletCards size={16} /></span>
        <strong>{at("partners_chart_payouts", {}, "Paid withdrawals")}</strong>
        <b>{money(payoutTotal)}</b>
      </header>
      <div class="admin-revenue-svg-frame admin-revenue-svg-frame--chart">
        <AdminRevenueChart
          series={payoutSeries}
          plotHeight={PLOT_HEIGHT}
          fmtMoney={(value) => money(value)}
          {currency}
          legendTimeLabel={at("partners_chart_time", {}, "Time")}
          legendValueLabel={at("partners_chart_payout_value", {}, "Withdrawals")}
          legendDeltaLabel={at("partners_chart_delta", {}, "Change")}
          variant="bar"
        />
      </div>
    </section>
  </div>
</article>

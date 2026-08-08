<script lang="ts">
  import { tick } from "svelte";
  import uPlot from "uplot";
  import "uplot/dist/uPlot.min.css";
  import Plate from "$components/ui/plate.svelte";

  type RevenuePoint = { date: string; amount: number };
  type TooltipGeometry = {
    left: number;
    arrowLeft: number;
    visible: boolean;
  };
  /**
   * uPlot keeps the `Path2D` it rendered on the series as `_paths`. It is an
   * internal field, but it is the only way to highlight *exactly* the drawn
   * curve: re-deriving the monotone spline by hand drifts away from it around
   * local minima, which is what a hand-built quadratic used to do here.
   */
  type SeriesPathBundle = { stroke?: Path2D | null; fill?: Path2D | null };
  type Props = {
    /** `{ date: ISO date string, amount: number }[]` */
    series?: RevenuePoint[];
    /** Total plot height in CSS px (axes + canvas). */
    plotHeight?: number;
    fmtMoney?: (value: number, currency: string) => string;
    currency?: string;
    /** Readout: column header for the time (x) series */
    legendTimeLabel?: string;
    /** Readout: column header for the value (y) series */
    legendValueLabel?: string;
    /** Readout: column header for the change against the previous point */
    legendDeltaLabel?: string;
    /** Line/area for revenue, bars for payout-like series. */
    variant?: "area" | "bar";
  };

  let {
    series = [],
    plotHeight = 204,
    fmtMoney = (v, _currency) => String(v),
    currency = "RUB",
    legendTimeLabel = "Time",
    legendValueLabel = "Value",
    legendDeltaLabel = "Change",
    variant = "area",
  }: Props = $props();

  let hostEl = $state<HTMLDivElement | undefined>();
  let tooltipEl = $state<HTMLDivElement | null>(null);
  let highlightCanvas = $state<HTMLCanvasElement | undefined>();
  let plot: uPlot | undefined;
  let resizeObserver: ResizeObserver | undefined;
  let syncTimer = 0;
  /** Rebuild plot when legend copy changes (language), since series labels are init-only */
  let builtLegendSig = "";

  /** Hovered point, or the last point when the cursor is away from the plot. */
  let hoverIndex = $state(-1);
  /** Floating readout geometry, clamped to the chart's left and right edges. */
  let tooltip = $state<TooltipGeometry>({ left: 8, arrowLeft: 24, visible: false });
  /** Overlay canvas box in CSS px, matching uPlot's own canvas. */
  let overlaySize = $state({ width: 0, height: 0 });

  /**
   * The highlight travels in *index space*: a fractional index is eased toward
   * the hovered one and every frame re-clips uPlot's path around it, so the
   * bright chunk crawls along the curve instead of jumping between periods.
   */
  let renderedIndex = -1;
  let targetIndex = -1;
  let highlightRaf = 0;
  let highlightVisible = $state(false);

  const points = $derived(series.filter((point) => point && point.date));
  const activeIndex = $derived(
    hoverIndex >= 0 && hoverIndex < points.length ? hoverIndex : points.length - 1
  );
  const activePoint = $derived(points[activeIndex] || null);
  const previousPoint = $derived(activeIndex > 0 ? points[activeIndex - 1] || null : null);
  const activeDelta = $derived(
    activePoint && previousPoint ? Number(activePoint.amount) - Number(previousPoint.amount) : null
  );
  const isLive = $derived(hoverIndex >= 0);

  function readCssColor(name: string, fallback: string): string {
    if (typeof document === "undefined") return fallback;
    const scope = hostEl || document.documentElement;
    const raw = getComputedStyle(scope).getPropertyValue(name).trim();
    return raw || fallback;
  }

  function parseDayUnix(iso: string): number {
    const s = String(iso || "");
    const t = Date.parse(s.includes("T") ? s : `${s}T12:00:00Z`);
    if (!Number.isFinite(t)) return 0;
    return Math.floor(t / 1000);
  }

  function toAlignedData(rows: RevenuePoint[]): uPlot.AlignedData | null {
    if (!rows?.length) return null;
    const xs = rows.map((p) => parseDayUnix(p.date));
    const ys = rows.map((p) => Number(p.amount) || 0);
    return [xs, ys];
  }

  function formatReadoutDate(iso: string): string {
    const parsed = new Date(iso.includes("T") ? iso : `${iso}T12:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return iso;
    return new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" }).format(parsed);
  }

  function formatDelta(value: number): string {
    const sign = value > 0 ? "+" : value < 0 ? "−" : "";
    return `${sign}${fmtMoney(Math.abs(value), currency)}`;
  }

  function clamp(value: number, min: number, max: number): number {
    return Math.min(Math.max(value, min), Math.max(min, max));
  }

  function cssPixelRatio(): number {
    return typeof devicePixelRatio === "number" && devicePixelRatio > 0 ? devicePixelRatio : 1;
  }

  function prefersReducedMotion(): boolean {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
  }

  function seriesPaths(u: uPlot): SeriesPathBundle | null {
    const line = u.series[1] as unknown as { _paths?: SeriesPathBundle | null } | undefined;
    return line?._paths ?? null;
  }

  /** Canvas-space (device px) position of a fractional index along the data. */
  function canvasPositionAt(u: uPlot, index: number): { x: number; y: number } {
    const xs = u.data[0];
    const ys = u.data[1];
    const last = xs.length - 1;
    const lower = clamp(Math.floor(index), 0, last);
    const upper = clamp(lower + 1, 0, last);
    const frac = clamp(index - lower, 0, 1);
    const xValue = Number(xs[lower]) + (Number(xs[upper]) - Number(xs[lower])) * frac;
    const yLow = u.valToPos(Number(ys[lower]) || 0, "y", true);
    const yHigh = u.valToPos(Number(ys[upper]) || 0, "y", true);
    return { x: u.valToPos(xValue, "x", true), y: yLow + (yHigh - yLow) * frac };
  }

  /** Width of one x step in CSS px. */
  function stepWidth(u: uPlot): number {
    const xs = u.data[0];
    if (!xs || xs.length < 2) return Math.max(12, u.bbox.width / cssPixelRatio() / 2);
    const first = u.valToPos(Number(xs[0]), "x");
    const last = u.valToPos(Number(xs[xs.length - 1]), "x");
    return Math.abs(last - first) / (xs.length - 1);
  }

  /**
   * Re-draws the highlight on the overlay canvas. It clips uPlot's own stroke
   * and fill paths to a window around the active point, so the bright line and
   * the lit area under it sit on the curve by construction.
   */
  function renderHighlight(): void {
    const u = plot;
    const canvas = highlightCanvas;
    if (!u || !canvas) return;

    const ratio = cssPixelRatio();
    const deviceWidth = Math.round(u.width * ratio);
    const deviceHeight = Math.round(u.height * ratio);
    if (canvas.width !== deviceWidth || canvas.height !== deviceHeight) {
      canvas.width = deviceWidth;
      canvas.height = deviceHeight;
    }
    if (overlaySize.width !== u.width || overlaySize.height !== u.height) {
      overlaySize = { width: u.width, height: u.height };
    }

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (renderedIndex < 0 || !u.data[0]?.length) return;

    const paths = seriesPaths(u);
    if (!paths?.stroke) return;

    const accent = readCssColor("--accent", "#00fe7a");
    const plotLeft = u.bbox.left;
    const plotRight = u.bbox.left + u.bbox.width;
    const plotTop = u.bbox.top;
    const plotHeightPx = u.bbox.height;
    const point = canvasPositionAt(u, renderedIndex);
    const stepPx = stepWidth(u) * ratio;
    const halfWindow =
      variant === "bar" ? Math.max(stepPx * 0.55, 9 * ratio) : Math.max(stepPx * 0.92, 15 * ratio);
    const windowLeft = clamp(point.x - halfWindow, plotLeft, plotRight);
    const windowRight = clamp(point.x + halfWindow, plotLeft, plotRight);
    const windowWidth = Math.max(1, windowRight - windowLeft);

    ctx.save();
    ctx.beginPath();
    ctx.rect(windowLeft, plotTop, windowWidth, plotHeightPx);
    ctx.clip();

    // A bar carries far less ink than an area curve, so the hovered column gets
    // a wash behind it; the area chart lights only the ink under its own curve.
    if (variant === "bar") {
      const columnGradient = ctx.createLinearGradient(0, plotTop, 0, plotTop + plotHeightPx);
      columnGradient.addColorStop(0, accent);
      columnGradient.addColorStop(1, "transparent");
      ctx.globalAlpha = 0.18;
      ctx.fillStyle = columnGradient;
      ctx.fillRect(windowLeft, plotTop, windowWidth, plotHeightPx);
    }

    // The area under the highlighted stretch, brighter than the base gradient.
    if (paths.fill) {
      const areaGradient = ctx.createLinearGradient(0, plotTop, 0, plotTop + plotHeightPx);
      areaGradient.addColorStop(0, accent);
      areaGradient.addColorStop(0.32, accent);
      areaGradient.addColorStop(1, "transparent");
      ctx.globalAlpha = variant === "bar" ? 0.72 : 0.46;
      ctx.fillStyle = areaGradient;
      ctx.fill(paths.fill);
    }

    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.strokeStyle = accent;
    ctx.globalAlpha = 0.24;
    ctx.lineWidth = (variant === "bar" ? 6 : 10) * ratio;
    ctx.stroke(paths.stroke);
    ctx.globalAlpha = 1;
    ctx.shadowColor = accent;
    ctx.shadowBlur = 10 * ratio;
    ctx.lineWidth = (variant === "bar" ? 2 : 3.4) * ratio;
    ctx.stroke(paths.stroke);
    ctx.shadowBlur = 0;
    ctx.restore();

    // Feather the window edges so the lit stretch fades into the base curve
    // instead of ending on two hard vertical cuts.
    ctx.save();
    ctx.globalCompositeOperation = "destination-out";
    const fade = ctx.createLinearGradient(windowLeft, 0, windowLeft + windowWidth, 0);
    fade.addColorStop(0, "rgba(0,0,0,1)");
    fade.addColorStop(0.24, "rgba(0,0,0,0)");
    fade.addColorStop(0.76, "rgba(0,0,0,0)");
    fade.addColorStop(1, "rgba(0,0,0,1)");
    ctx.fillStyle = fade;
    ctx.fillRect(windowLeft, plotTop, windowWidth, plotHeightPx);
    ctx.restore();

    // Thin dashed drop line from the point down past the axis, so the readout
    // card below reads as belonging to this period.
    ctx.save();
    ctx.globalAlpha = 0.6;
    ctx.strokeStyle = accent;
    ctx.lineWidth = Math.max(1, ratio);
    ctx.setLineDash([2 * ratio, 4 * ratio]);
    ctx.beginPath();
    ctx.moveTo(point.x, point.y + 7 * ratio);
    ctx.lineTo(point.x, canvas.height);
    ctx.stroke();
    ctx.restore();

    // The vertex marker is drawn only once the highlight has settled: between
    // periods the interpolated position is a chord, not the spline, so a
    // travelling dot would visibly cut the corner. The glow flows; the marker
    // — which stands for one discrete value — lands.
    if (variant !== "bar" && Math.abs(targetIndex - renderedIndex) < 0.02) {
      ctx.save();
      ctx.shadowColor = accent;
      ctx.shadowBlur = 9 * ratio;
      ctx.fillStyle = accent;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 4.2 * ratio, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = readCssColor("--admin-bg", "#05100b");
      ctx.beginPath();
      ctx.arc(point.x, point.y, 1.7 * ratio, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }

  function stepHighlight(): void {
    highlightRaf = 0;
    if (targetIndex < 0) return;
    const distance = targetIndex - renderedIndex;
    if (Math.abs(distance) < 0.01) {
      renderedIndex = targetIndex;
      renderHighlight();
      return;
    }
    renderedIndex += distance * 0.28;
    renderHighlight();
    highlightRaf = requestAnimationFrame(stepHighlight);
  }

  function moveHighlight(index: number): void {
    targetIndex = index;
    if (renderedIndex < 0 || prefersReducedMotion()) {
      renderedIndex = index;
      renderHighlight();
      return;
    }
    if (!highlightRaf) highlightRaf = requestAnimationFrame(stepHighlight);
  }

  function clearHighlight(): void {
    cancelAnimationFrame(highlightRaf);
    highlightRaf = 0;
    targetIndex = -1;
    renderedIndex = -1;
    highlightVisible = false;
    renderHighlight();
  }

  function positionTooltip(u: uPlot, index: number): void {
    const ratio = cssPixelRatio();
    const pointX = canvasPositionAt(u, index).x / ratio;
    const tooltipWidth = tooltipEl?.offsetWidth || 190;
    const chartWidth = hostEl?.clientWidth || u.width;
    const edgeGap = 8;
    const left = clamp(pointX - tooltipWidth / 2, edgeGap, chartWidth - tooltipWidth - edgeGap);
    const arrowLeft = clamp(pointX - left, 16, tooltipWidth - 16);
    tooltip = { left, arrowLeft, visible: true };
  }

  function yAxisTickLabels(values: number[]): string[] {
    return values.map((v) => fmtMoney(Number(v), currency));
  }

  /** uPlot passes already-formatted tick strings; reserve enough gutter so amounts are not clipped */
  function yAxisGutterWidth(_u: uPlot, values: string[] | null): number {
    const pad = 14;
    const charPx = 6.1;
    const maxChars = (values || []).reduce((m, v) => Math.max(m, String(v ?? "").length), 0);
    return Math.min(104, Math.max(58, Math.ceil(pad + maxChars * charPx)));
  }

  /** Axis `size`: height (x / bottom) or width (y / left) in CSS px — only customize the y gutter */
  function axisBandSize(_u: uPlot, values: string[] | null, axisIdx: number): number {
    if (axisIdx !== 1) return 32;
    return yAxisGutterWidth(_u, values);
  }

  function trackCursor(u: uPlot): void {
    const idx = u.cursor.idx;
    if (idx == null || idx < 0) {
      hoverIndex = -1;
      tooltip = { ...tooltip, visible: false };
      clearHighlight();
      return;
    }
    hoverIndex = idx;
    highlightVisible = true;
    positionTooltip(u, idx);
    moveHighlight(idx);
  }

  function buildOpts(width: number): uPlot.Options {
    const w = Math.max(80, Math.floor(width));
    const muted = readCssColor("--admin-muted", "#9aa7a2");
    const border = readCssColor("--admin-border", "rgba(255,255,255,0.12)");
    const accent = readCssColor("--accent", "#00fe7a");
    const lineStroke = readCssColor("--admin-chart-stroke", accent);
    const configuredFill = readCssColor("--admin-chart-fill", "rgba(0, 254, 122, 0.38)");
    const gradientStart = readCssColor("--admin-chart-gradient-start", configuredFill);
    const gradientEnd = readCssColor("--admin-chart-gradient-end", "rgba(0, 254, 122, 0)");
    const chartFill = (plotInstance: uPlot): CanvasGradient => {
      const gradient = plotInstance.ctx.createLinearGradient(
        0,
        plotInstance.bbox.top,
        0,
        plotInstance.bbox.top + plotInstance.bbox.height
      );
      gradient.addColorStop(0, gradientStart);
      gradient.addColorStop(1, gradientEnd);
      return gradient;
    };

    return {
      width: w,
      height: plotHeight,
      class: "admin-uplot",
      pxAlign: true,
      padding: [10, 12, 12, 10],
      // The floating card below the plot replaces uPlot's live legend, and the
      // overlay draws its own drop line, so uPlot's crosshair stays off.
      legend: { show: false },
      cursor: {
        drag: { x: false, y: false },
        points: { size: 8, width: 2, stroke: accent, fill: accent },
      },
      hooks: {
        setCursor: [trackCursor],
        setSize: [() => renderHighlight()],
        // Series paths are rebuilt on every redraw; the overlay clips them, so
        // it has to be repainted from the fresh ones.
        draw: [() => renderHighlight()],
      },
      scales: {
        x: { time: true },
        y:
          variant === "bar"
            ? { range: [0, null] }
            : {
                range: (_u: uPlot, min: number, max: number) => [
                  Math.min(0, Number.isFinite(min) ? min : 0),
                  Math.max(0, Number.isFinite(max) ? max : 0),
                ],
              },
      },
      series: [
        { label: legendTimeLabel },
        {
          label: legendValueLabel,
          paths:
            variant === "bar"
              ? uPlot.paths.bars?.({ size: [0.68, 48], radius: [0.22, 0] })
              : uPlot.paths.spline?.(),
          stroke: lineStroke,
          width: variant === "bar" ? 1 : 2,
          cap: "round",
          fill: chartFill,
          points: { show: variant !== "bar" },
        },
      ],
      axes: [
        {
          stroke: muted,
          gap: 8,
          grid: { show: true, stroke: border, width: 1 },
          ticks: { stroke: border },
          font: "11px system-ui,Segoe UI,sans-serif",
        },
        {
          stroke: muted,
          size: axisBandSize,
          gap: 8,
          grid: { show: true, stroke: border, width: 1 },
          ticks: { stroke: border },
          font: "10px system-ui,Segoe UI,sans-serif",
          values: (_u: uPlot, ticks: number[]) => yAxisTickLabels(ticks),
        },
      ],
    };
  }

  function syncChart() {
    if (!hostEl) return;
    const d = toAlignedData(series);
    const legendSig = `${legendTimeLabel}\0${legendValueLabel}\0${variant}`;
    if (!d) {
      plot?.destroy();
      plot = undefined;
      builtLegendSig = "";
      return;
    }
    const w = Math.max(80, Math.floor(hostEl.clientWidth));
    if (plot && builtLegendSig !== legendSig) {
      plot.destroy();
      plot = undefined;
    }
    if (!plot) {
      plot = new uPlot(buildOpts(w), d, hostEl);
      builtLegendSig = legendSig;
      renderHighlight();
      return;
    }
    plot.setData(d, true);
    plot.setSize({ width: w, height: plotHeight });
  }

  function scheduleSync() {
    if (typeof window === "undefined") return;
    clearTimeout(syncTimer);
    syncTimer = window.setTimeout(() => {
      syncTimer = 0;
      syncChart();
    }, 0);
  }

  let rafId = 0;

  function attachChartHost(node: HTMLDivElement): () => void {
    hostEl = node;
    rafId = requestAnimationFrame(() => {
      void tick().then(() => {
        scheduleSync();
        if (hostEl !== node || typeof ResizeObserver === "undefined") return;
        resizeObserver = new ResizeObserver(() => scheduleSync());
        resizeObserver.observe(node);
      });
    });
    return () => {
      cancelAnimationFrame(rafId);
      cancelAnimationFrame(highlightRaf);
      highlightRaf = 0;
      clearTimeout(syncTimer);
      resizeObserver?.disconnect();
      resizeObserver = undefined;
      plot?.destroy();
      plot = undefined;
      builtLegendSig = "";
      hoverIndex = -1;
      renderedIndex = -1;
      targetIndex = -1;
      highlightVisible = false;
      overlaySize = { width: 0, height: 0 };
      tooltip = { ...tooltip, visible: false };
      if (hostEl === node) hostEl = undefined;
    };
  }

  $effect(() => {
    if (!hostEl) return;
    series;
    plotHeight;
    fmtMoney;
    currency;
    legendTimeLabel;
    legendValueLabel;
    variant;
    scheduleSync();
  });
</script>

<div class="admin-revenue-chart-body">
  <div class="admin-revenue-uplot-wrap">
    <div class="admin-revenue-uplot-host" {@attach attachChartHost}></div>
    <canvas
      bind:this={highlightCanvas}
      class="admin-chart-highlight"
      class:is-visible={highlightVisible}
      style={`width:${overlaySize.width}px; height:${overlaySize.height}px;`}
      aria-hidden="true"
    ></canvas>
  </div>

  <!-- A fixed-height lane keeps the layout stable. The card follows the active
       period only horizontally; its arrow remains aligned when clamped. -->
  <div class="admin-chart-tooltip-lane">
    <Plate
      bind:ref={tooltipEl}
      class="admin-chart-tooltip"
      arrow="top"
      arrowX={`${tooltip.arrowLeft}px`}
      visible={tooltip.visible && isLive}
      style={`transform: translate3d(${tooltip.left}px, 0, 0);`}
      role="status"
      aria-live="off"
    >
      <div class="admin-chart-tooltip-meta">
        <span>{activePoint ? formatReadoutDate(activePoint.date) : "—"}</span>
        <span
          class:is-up={activeDelta !== null && activeDelta > 0}
          class:is-down={activeDelta !== null && activeDelta < 0}
          title={legendDeltaLabel}
        >
          {activeDelta === null ? "—" : formatDelta(activeDelta)}
        </span>
      </div>
      <div class="admin-chart-tooltip-value">
        <small>{legendValueLabel}</small>
        <strong>{activePoint ? fmtMoney(Number(activePoint.amount), currency) : "—"}</strong>
      </div>
      <span class="sr-only">{legendTimeLabel}</span>
    </Plate>
  </div>
</div>

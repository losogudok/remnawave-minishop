export type ChartAlignedData = [x: number[], y: number[]];
export type ChartVariant = "area" | "bar";

export const CHART_MORPH_DURATION_MS = 360;
export const CHART_REVEAL_DURATION_MS = 520;

function clampUnit(value: number): number {
  return Math.min(1, Math.max(0, value));
}

export function easeChartMotion(progress: number): number {
  const value = clampUnit(progress);
  return value * value * (3 - 2 * value);
}

function finiteNumber(value: number | null | undefined): number {
  return Number.isFinite(value) ? Number(value) : 0;
}

function resample(values: readonly (number | null | undefined)[], count: number): number[] {
  if (count <= 0) return [];
  if (values.length === 0) return Array.from({ length: count }, () => 0);
  if (values.length === 1 || count === 1) {
    return Array.from({ length: count }, () => finiteNumber(values[0]));
  }

  const last = values.length - 1;
  return Array.from({ length: count }, (_, index) => {
    const position = (index * last) / (count - 1);
    const lower = Math.floor(position);
    const upper = Math.min(last, lower + 1);
    const fraction = position - lower;
    const start = finiteNumber(values[lower]);
    const end = finiteNumber(values[upper]);
    return start + (end - start) * fraction;
  });
}

/**
 * Resamples the old shape onto the target buckets, then interpolates only its
 * y values. Keeping target x values and point count stable is important for
 * uPlot: otherwise every animation frame changes the time scale, tick layout,
 * bar width, and plot bbox, which makes the whole chart visibly shake.
 */
export function morphChartData(
  from: ChartAlignedData,
  to: ChartAlignedData,
  progress: number,
  _variant: ChartVariant
): ChartAlignedData {
  const sampleCount = to[0].length;
  if (sampleCount === 0) return [[], []];

  const amount = easeChartMotion(progress);
  const fromY = resample(from[1], sampleCount);
  const toY = resample(to[1], sampleCount);

  return [to[0].slice(), fromY.map((value, index) => value + (toY[index] - value) * amount)];
}

/** Grow payout columns with a small left-to-right stagger on first entry. */
export function revealBarChartData(target: ChartAlignedData, progress: number): ChartAlignedData {
  const count = target[1].length;
  if (count === 0) return [target[0].slice(), []];

  const staggerWindow = count > 1 ? 0.24 : 0;
  const growWindow = 1 - staggerWindow;
  const y = target[1].map((value, index) => {
    const delay = count > 1 ? (index / (count - 1)) * staggerWindow : 0;
    const localProgress = clampUnit((progress - delay) / growWindow);
    return value * easeChartMotion(localProgress);
  });
  return [target[0].slice(), y];
}

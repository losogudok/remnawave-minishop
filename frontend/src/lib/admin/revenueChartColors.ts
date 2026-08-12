const DEFAULT_CHART_FILL_OPACITY = 0.38;

function colorWithAlpha(color: string, alpha: number): string {
  const normalizedColor = String(color || "").trim();
  const normalizedAlpha = Math.min(1, Math.max(0, alpha));
  const hexMatch = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(normalizedColor);

  if (!hexMatch) {
    return `color-mix(in srgb, ${normalizedColor} ${normalizedAlpha * 100}%, transparent)`;
  }

  const rawHex = hexMatch[1];
  const hex = rawHex.length === 3 ? [...rawHex].map((char) => char + char).join("") : rawHex;
  const red = Number.parseInt(hex.slice(0, 2), 16);
  const green = Number.parseInt(hex.slice(2, 4), 16);
  const blue = Number.parseInt(hex.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${normalizedAlpha})`;
}

export function revenueChartGradientFallbacks(
  stroke: string,
  configuredFill = ""
): { start: string; end: string } {
  const effectiveStroke = String(stroke || "").trim() || "#00fe7a";
  const explicitFill = String(configuredFill || "").trim();
  return {
    start: explicitFill || colorWithAlpha(effectiveStroke, DEFAULT_CHART_FILL_OPACITY),
    end: colorWithAlpha(effectiveStroke, 0),
  };
}

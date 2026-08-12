import { formatCompactNumber } from "./formatters.js";
import type { TermUnitLabel } from "./types.js";

type Translate = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

type PromoEffectSummaryDeps = {
  t: Translate;
  termUnitLabel: TermUnitLabel;
};

function positiveNumber(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

export function formatPromoEffectSummary(
  payload: Record<string, unknown>,
  { t, termUnitLabel }: PromoEffectSummaryDeps
): string {
  const parts: string[] = [];
  const bonusDays = positiveNumber(payload.bonus_days);
  const regularTrafficGb = positiveNumber(payload.regular_traffic_gb);
  const premiumTrafficGb = positiveNumber(payload.premium_traffic_gb);

  if (bonusDays !== null) {
    parts.push(
      t(
        "wa_promo_effect_bonus_days",
        {
          value: formatCompactNumber(bonusDays),
          unit: termUnitLabel(bonusDays, "day"),
        },
        "+{value} {unit}"
      )
    );
  }
  if (regularTrafficGb !== null) {
    parts.push(
      t(
        "wa_promo_effect_regular_traffic",
        { value: formatCompactNumber(regularTrafficGb) },
        "+{value} GB regular traffic"
      )
    );
  }
  if (premiumTrafficGb !== null) {
    parts.push(
      t(
        "wa_promo_effect_premium_traffic",
        { value: formatCompactNumber(premiumTrafficGb) },
        "+{value} GB premium traffic"
      )
    );
  }

  return parts.length ? parts.join(", ") : String(payload.effect_summary || "").trim();
}

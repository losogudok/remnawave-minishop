import type { ApiClient } from "./publicApi";
import type { components } from "../api/openapi.generated";
import { sanitizeOnboardingAttribution, type OnboardingAttribution } from "./onboardingSignals";

let context: components["schemas"]["AdPublicContextOut"] | null = null;
let observedAcquisition: OnboardingAttribution | null = null;

export function clearAdvertisingContext(): void {
  context = null;
  observedAcquisition = null;
}

export function advertisingCheckoutCode(): string {
  return context?.offer_available && context.offer_mode === "checkout"
    ? context.offer_code || ""
    : "";
}

/**
 * Sanitized acquisition metadata for onboarding signal integrations.
 * Only the campaign code and known utm values are exposed; raw URLs,
 * tokens and offer codes never leave this module. Reads the observed visit
 * params captured before URL cleanup (not the backend configured default),
 * so callers must run after `captureAdvertising` settles to keep
 * attribution ordered before URL cleanup. Existing advertising behavior
 * (context/checkout/offer) is unchanged.
 */
export function advertisingAttribution(): OnboardingAttribution {
  if (observedAcquisition) {
    return { code: observedAcquisition.code, utm: { ...observedAcquisition.utm } };
  }
  return sanitizeOnboardingAttribution({
    code: context?.code || "",
    utm: context?.utm || {},
  });
}

export async function captureAdvertising(api: ApiClient["api"]): Promise<typeof context> {
  context = null;
  observedAcquisition = null;
  const url = new URL(window.location.href);
  const params = url.searchParams;
  const launch = new URLSearchParams(window.location.hash.slice(1));
  const signed = new URLSearchParams(
    launch.get("tgWebAppData") || params.get("tgWebAppData") || ""
  );
  const code =
    ["campaign", "start", "startapp", "start_param", "tgWebAppStartParam"]
      .flatMap((key) =>
        [params, launch, signed].map((source) =>
          source.getAll(key).length === 1 ? source.get(key) || "" : ""
        )
      )
      .find(
        (value) =>
          /^[A-Za-z0-9_-]{2,64}$/.test(value) &&
          !/^(ref_|p_|promo_|plan_|gift_|admin_|ticket_|webapp_auth_)/i.test(value)
      ) || "";
  const utm: Record<string, string> = {};
  for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) {
    if (params.getAll(key).length === 1 && params.get(key)) utm[key] = params.get(key) || "";
  }
  // Capture + sanitize the observed visit acquisition before any URL cleanup,
  // so the actual visit source survives even when the backend returns a
  // configured default. This does not change existing context/checkout/offer
  // behavior below.
  try {
    observedAcquisition = sanitizeOnboardingAttribution({ code, utm });
  } catch {
    observedAcquisition = null;
  }
  const fingerprint = JSON.stringify({ code, utm });
  const previous = window.history.state as Record<string, unknown> | null;
  const eventId =
    previous?.adFingerprint === fingerprint && typeof previous.adEventId === "string"
      ? previous.adEventId
      : globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const state = { ...previous, adFingerprint: fingerprint, adEventId: eventId };
  try {
    const result =
      code || Object.keys(utm).length
        ? await api("/advertising/capture", {
            method: "POST",
            body: JSON.stringify({ code, utm, event_id: eventId }),
            signal: AbortSignal.timeout(5000),
          })
        : await api("/advertising/context", { signal: AbortSignal.timeout(5000) });
    if (result.ok === true) {
      context = result.context || null;
      if (result.captured && code && context) {
        for (const key of ["campaign", "start", "startapp", "start_param", "tgWebAppStartParam"]) {
          if (params.get(key) === code) params.delete(key);
        }
        for (const key of Object.keys(utm)) params.delete(key);
        window.history.replaceState(state, "", url);
      }
    }
  } catch {
    // An unavailable analytics endpoint must not stop authentication or checkout.
  }
  return context;
}

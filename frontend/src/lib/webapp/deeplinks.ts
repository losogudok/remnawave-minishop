import { normalizedEmail } from "./formatters.js";
import { PLANS_PATH, stripRoutePrefix } from "./routes.js";

export type RenewalDeeplink = {
  tariffKey: string;
};

type TelegramWindow = Window & {
  Telegram?: {
    WebApp?: {
      initDataUnsafe?: {
        start_param?: string;
      };
    };
  };
};

export function currentSearchParams() {
  return new URLSearchParams(window.location.search);
}

export function readEmailCodeLoginDeeplink() {
  const params = currentSearchParams();
  if (params.get("login") !== "email_code") return null;
  const emailHint = normalizedEmail(params.get("login_email") || "");
  if (!emailHint || !emailHint.includes("@")) return null;
  return emailHint;
}

export function hasEmailCodeLoginDeeplink() {
  return Boolean(readEmailCodeLoginDeeplink());
}

export function readRenewalDeeplink(): RenewalDeeplink | null {
  const params = currentSearchParams();
  const shouldRenew = params.get("after_login") === "renew" || params.get("renew") === "1";
  if (!shouldRenew) return null;
  return {
    tariffKey: String(params.get("renew_tariff") || "").trim(),
  };
}

export function stripRenewalLoginQueryFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const keys = ["login", "login_email", "after_login", "renew", "renew_tariff"];
  const changed = keys.some((key) => url.searchParams.has(key));
  if (!changed) return;
  for (const key of keys) url.searchParams.delete(key);
  const search = url.searchParams.toString();
  window.history.replaceState(null, "", `${url.pathname}${search ? `?${search}` : ""}${url.hash}`);
}

function telegramStartParam(): string {
  if (typeof window === "undefined") return "";
  return (
    ((window as TelegramWindow).Telegram?.WebApp?.initDataUnsafe?.start_param as string | undefined)
      ?.trim()
      .toString() || ""
  );
}

function normalizeCheckoutPromoParam(value: string | null): string {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const lower = raw.toLowerCase();
  return lower.startsWith("promo_") ? raw.slice("promo_".length).trim() : raw;
}

/**
 * A start parameter is a promo code only when it says so.
 *
 * The same parameter also carries app routes (`plans`, `invite`, `ticket_7`),
 * and treating one of those as a code made the app open checkout and complain
 * about an invalid promo instead of going where the link pointed.
 */
function startParamPromoCode(value: string | null): string {
  const raw = String(value || "").trim();
  return raw.toLowerCase().startsWith("promo_") ? raw.slice("promo_".length).trim() : "";
}

export function readCheckoutPromoDeeplink(): string {
  const params = currentSearchParams();
  return (
    normalizeCheckoutPromoParam(params.get("promo_code")) ||
    normalizeCheckoutPromoParam(params.get("promo")) ||
    startParamPromoCode(params.get("startapp")) ||
    startParamPromoCode(params.get("start_param")) ||
    startParamPromoCode(params.get("tgWebAppStartParam")) ||
    startParamPromoCode(telegramStartParam())
  );
}

export function stripCheckoutPromoQueryFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const keys = ["promo", "promo_code", "startapp", "start_param", "tgWebAppStartParam"];
  const changed = keys.some((key) => url.searchParams.has(key));
  if (!changed) return;
  for (const key of keys) url.searchParams.delete(key);
  const search = url.searchParams.toString();
  window.history.replaceState(null, "", `${url.pathname}${search ? `?${search}` : ""}${url.hash}`);
}

/** Marks the checkout route, which opens plan selection over the home screen. */
export function isPlansRoute(pathname: string, routePrefix = ""): boolean {
  return stripRoutePrefix(pathname, routePrefix).toLowerCase().replace(/\/+$/, "") === PLANS_PATH;
}

export function stripTopupQueryFromUrl() {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (!url.searchParams.has("topup")) return;
  url.searchParams.delete("topup");
  const search = url.searchParams.toString();
  window.history.replaceState(null, "", `${url.pathname}${search ? `?${search}` : ""}${url.hash}`);
}

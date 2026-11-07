import { render } from "svelte/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import en from "../../../../locales/en.json";
import ru from "../../../../locales/ru.json";
import AuthScreen from "./AuthScreenTestHarness.svelte";

const INTRO_FALLBACK = "Sign in to manage your subscription, payments, and devices.";
const action = () => {};

function installBrowser() {
  vi.stubGlobal("window", {
    location: { origin: "https://shop.example", hash: "" },
    Telegram: { WebApp: { initData: "" } },
  });
}

function renderGuest(
  t: (key: string, params?: Record<string, unknown>, fallback?: string) => string
) {
  return render(AuthScreen, {
    props: {
      CFG: {
        authProviders: ["telegram", "google", "email"],
        emailAuthEnabled: true,
      },
      brandTitle: "Minishop",
      clearLoginEmailError: action,
      loginWithEmailPassword: action,
      onBackToLogin: action,
      openExternalLink: action,
      openTelegramLogin: action,
      requestEmailCode: action,
      setPasswordLoginMode: action,
      submitEmailOnEnter: action,
      verifyEmailCode: action,
      t,
    },
  }).body;
}

afterEach(() => vi.unstubAllGlobals());

describe("login introduction", () => {
  it("ships a default intro in both base locales", () => {
    for (const locale of [ru, en] as Record<string, string>[]) {
      expect(locale.wa_login_intro?.trim()).toBeTruthy();
    }
    expect((en as Record<string, string>).wa_login_intro).toBe(INTRO_FALLBACK);
  });

  it("shows the intro as a guest without breaking login methods", () => {
    for (const locale of [ru, en] as Record<string, string>[]) {
      installBrowser();
      const t = (key: string) => locale[key] || key;
      const html = renderGuest(t);
      expect(html).toContain(locale.wa_login_intro);
      expect(html).toContain("auth-intro");
      // The intro stays inside the branding block above the auth card, so it
      // follows the shared .login-brand p styling in every login mode.
      const brandPos = html.indexOf("login-brand-auth");
      const introPos = html.indexOf("auth-intro");
      const cardPos = html.indexOf('class="card auth-card"');
      expect(brandPos).toBeGreaterThanOrEqual(0);
      expect(cardPos).toBeGreaterThanOrEqual(0);
      expect(brandPos).toBeLessThan(introPos);
      expect(introPos).toBeLessThan(cardPos);
      expect(html).toContain('data-auth-provider="telegram"');
      expect(html).toContain('data-auth-provider="google"');
      // Non-compact guests get the email form instead of an icon button.
      expect(html).toContain('type="email"');
      expect(html).toContain(locale.wa_send_code_email);
      vi.unstubAllGlobals();
    }
  });

  it("keeps the email provider button usable in compact login mode", () => {
    installBrowser();
    const locale: Record<string, string> = en;
    const html = render(AuthScreen, {
      props: {
        CFG: {
          authProviders: ["telegram", "email"],
          compactLoginEnabled: true,
          emailAuthEnabled: true,
          wideAuthProviders: ["telegram"],
        },
        brandTitle: "Minishop",
        clearLoginEmailError: action,
        loginWithEmailPassword: action,
        onBackToLogin: action,
        openExternalLink: action,
        openTelegramLogin: action,
        requestEmailCode: action,
        setPasswordLoginMode: action,
        submitEmailOnEnter: action,
        verifyEmailCode: action,
        t: (key: string) => locale[key] || key,
      },
    }).body;
    expect(html).toContain("auth-intro");
    expect(html).toContain('data-auth-provider="telegram"');
    expect(html).toContain('data-auth-provider="email"');
  });

  it("hides the intro block when the copy is explicitly empty", () => {
    installBrowser();
    const t = (key: string) => (key === "wa_login_intro" ? "" : key);
    const html = renderGuest(t);
    expect(html).not.toContain("auth-intro");
    expect(html).toContain('data-auth-provider="telegram"');
    expect(html).toContain('data-auth-provider="google"');
    expect(html).toContain('type="email"');
  });

  it("renders the intro as text without executing markup", () => {
    installBrowser();
    const payload = `<script>alert(1)</script><img src=x onerror=alert(2)>`;
    const t = (key: string) => (key === "wa_login_intro" ? payload : key);
    const html = renderGuest(t);
    expect(html).toContain("auth-intro");
    expect(html).not.toContain("<script>alert(1)</script>");
    // Svelte escapes `<`/`&` in text nodes, so the payload stays inert text.
    expect(html).toContain("&lt;script>alert(1)&lt;/script>");
    expect(html).toContain("&lt;img src=x onerror=alert(2)>");
    expect(html).toContain('data-auth-provider="telegram"');
    expect(html).toContain('data-auth-provider="google"');
    expect(html).toContain('type="email"');
  });

  it("uses the defined English fallback when the key is missing", () => {
    installBrowser();
    const t = (_key: string, _params?: Record<string, unknown>, fallback = "") => fallback;
    const html = renderGuest(t);
    expect(html).toContain(INTRO_FALLBACK);
    expect(html).toContain("auth-intro");
  });
});

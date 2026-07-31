import { afterEach, describe, expect, it, vi } from "vitest";

import { isPlansRoute, readCheckoutPromoDeeplink } from "./deeplinks.js";

function withSearch(search: string) {
  vi.stubGlobal("window", { location: { search } });
}

describe("checkout promo deeplinks", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads a prefixed code out of every start parameter", () => {
    for (const key of ["startapp", "start_param", "tgWebAppStartParam"]) {
      withSearch(`?${key}=promo_SAVE10`);
      expect(readCheckoutPromoDeeplink()).toBe("SAVE10");
    }
  });

  it("still accepts a bare code from the explicit promo parameters", () => {
    withSearch("?promo_code=SAVE10");
    expect(readCheckoutPromoDeeplink()).toBe("SAVE10");
    withSearch("?promo=SAVE10");
    expect(readCheckoutPromoDeeplink()).toBe("SAVE10");
  });

  it("never mistakes an app route in a start parameter for a code", () => {
    // These payloads name where to go. Reading one as a promo code opened
    // checkout and complained the code was invalid instead of navigating.
    for (const payload of ["plans", "invite", "support", "ticket_7", "admin_user_5"]) {
      withSearch(`?startapp=${payload}`);
      expect(readCheckoutPromoDeeplink()).toBe("");
    }
  });
});

describe("checkout route", () => {
  it("recognises the route with and without a mount prefix", () => {
    expect(isPlansRoute("/plans")).toBe(true);
    expect(isPlansRoute("/plans/")).toBe(true);
    expect(isPlansRoute("/demo/runtime/plans", "/demo/runtime")).toBe(true);
    expect(isPlansRoute("/home")).toBe(false);
    expect(isPlansRoute("/plans/extra")).toBe(false);
  });
});

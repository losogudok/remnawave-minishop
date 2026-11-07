import { afterEach, describe, expect, it, vi } from "vitest";
import { advertisingAttribution, captureAdvertising, clearAdvertisingContext } from "./advertising";
import type { ApiClient } from "./publicApi";

afterEach(() => {
  clearAdvertisingContext();
  vi.unstubAllGlobals();
});

describe("advertising attribution ordering", () => {
  it("preserves the observed visit source after cleanup instead of the configured default", async () => {
    const replaceState = vi.fn();
    vi.stubGlobal("window", {
      location: {
        href: "https://app.example/?utm_source=actual-source&utm_medium=cpc",
        hash: "",
      },
      history: { state: null, replaceState },
    });
    const api = (async () => ({
      ok: true,
      captured: true,
      context: {
        code: "",
        utm: { utm_source: "configured-default" },
        offer_available: false,
        offer_mode: "",
        offer_code: "",
      },
    })) as unknown as ApiClient["api"];
    await captureAdvertising(api);
    const attribution = advertisingAttribution();
    expect(attribution.utm.utm_source).toBe("actual-source");
    expect(attribution.utm.utm_source).not.toBe("configured-default");
    expect(JSON.stringify(attribution)).not.toContain("configured-default");
  });
});

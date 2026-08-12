import { describe, expect, it } from "vitest";

import { deviceGlyphPaths, resolveDeviceGlyph } from "./deviceGlyph.js";

describe("resolveDeviceGlyph", () => {
  it("reads the form factor from the model and the OS from the platform label", () => {
    expect(
      resolveDeviceGlyph({ display_name: "iPhone 15 Pro", platform_label: "iOS 18.4" })
    ).toEqual({ shape: "phone", os: "apple" });
    expect(
      resolveDeviceGlyph({ display_name: "MacBook Air", platform_label: "macOS 15.4" })
    ).toEqual({ shape: "laptop", os: "apple" });
    expect(resolveDeviceGlyph({ display_name: "iPad Pro", platform_label: "iPadOS 18.4" })).toEqual(
      {
        shape: "tablet",
        os: "apple",
      }
    );
    expect(
      resolveDeviceGlyph({ display_name: "Windows Laptop", platform_label: "Windows 11" })
    ).toEqual({ shape: "laptop", os: "windows" });
    expect(
      resolveDeviceGlyph({ display_name: "Ubuntu Desktop", platform_label: "Ubuntu 24.04" })
    ).toEqual({ shape: "desktop", os: "linux" });
  });

  it("prefers Android over the Linux its user agent advertises", () => {
    expect(
      resolveDeviceGlyph({
        display_name: "Pixel 9",
        platform: "android",
        user_agent: "Mozilla/5.0 (Linux; Android 15; Pixel 9)",
      })
    ).toEqual({ shape: "phone", os: "android" });
  });

  it("keeps an iPad a tablet even though its user agent claims Macintosh", () => {
    expect(
      resolveDeviceGlyph({
        display_name: "iPad Air",
        user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      })
    ).toEqual({ shape: "tablet", os: "apple" });
  });

  it("treats a bare Mac as a computer rather than a phone", () => {
    expect(resolveDeviceGlyph({ platform: "macos", os_version: "15.4" })).toEqual({
      shape: "laptop",
      os: "apple",
    });
  });

  it("only matches whole words, so unrelated text keeps the neutral fallback", () => {
    expect(
      resolveDeviceGlyph({ display_name: "Studios Notepad", user_agent: "Happ/3.1.0" })
    ).toEqual({ shape: "phone", os: "unknown" });
    expect(resolveDeviceGlyph(null)).toEqual({ shape: "phone", os: "unknown" });
  });

  it("falls back to the shape each OS is most often used on", () => {
    expect(resolveDeviceGlyph({ platform: "windows" }).shape).toBe("laptop");
    expect(resolveDeviceGlyph({ platform: "android" }).shape).toBe("phone");
    expect(resolveDeviceGlyph({ platform: "linux" }).shape).toBe("desktop");
  });
});

describe("deviceGlyphPaths", () => {
  it("scales the OS mark into the shell's screen box", () => {
    const phone = deviceGlyphPaths({ display_name: "iPhone 15 Pro" });
    expect(phone.outline.length).toBeGreaterThan(0);
    expect(phone.mark.length).toBeGreaterThan(0);
    // Screen box is 9 units wide centred at (12, 12) on the 24-grid.
    expect(phone.markTransform).toBe("translate(7.5 7.5) scale(0.9)");
  });

  it("draws no mark when the OS is unknown", () => {
    const unknown = deviceGlyphPaths({ display_name: "Device 3" });
    expect(unknown.os).toBe("unknown");
    expect(unknown.mark).toEqual([]);
    expect(unknown.outline.length).toBeGreaterThan(0);
  });
});

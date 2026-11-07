import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("PWA install dialog", () => {
  it("portals the shared Dialog out of transformed home containers", () => {
    const source = readFileSync(new URL("./PwaInstallDialog.svelte", import.meta.url), "utf8");
    const dialogIndex = source.indexOf("<Dialog");
    expect(dialogIndex).toBeGreaterThanOrEqual(0);
    const openingTag = source.slice(dialogIndex, dialogIndex + 600);
    expect(openingTag).toMatch(/\bportal\b/);
    expect(openingTag).toContain("onclose");
    expect(openingTag).toContain("closeLabel");
  });
});

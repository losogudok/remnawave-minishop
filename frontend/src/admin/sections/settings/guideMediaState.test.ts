import { describe, expect, it } from "vitest";

import {
  catalogProblems,
  catalogState,
  orphanState,
  type GuideMediaCatalogConfig,
} from "./guideMediaState";

function app(overrides: Record<string, unknown> = {}) {
  return { id: "client", name: "Client", explicit_id: true, ambiguous: false, ...overrides };
}

function config(overrides: Partial<GuideMediaCatalogConfig> = {}): GuideMediaCatalogConfig {
  return {
    config_id: "local:file",
    source: "file",
    platforms: [],
    missing: false,
    error: null,
    ...overrides,
  };
}

describe("guide media admin state", () => {
  it("distinguishes an unavailable catalog from an empty one", () => {
    expect(catalogState([], false)).toBe("unavailable");
    expect(catalogState([], true)).toBe("empty");
    expect(catalogState([config()], true)).toBe("empty");
    expect(
      catalogState(
        [
          config({
            platforms: [{ id: "ios", label: "iOS", apps: [app()] }],
          }),
        ],
        true
      )
    ).toBe("ready");
  });

  it("lists failed sources independently of the selected configuration", () => {
    const problems = catalogProblems([
      config({ config_id: "local:file", missing: true, error: "local config is broken" }),
      config({
        config_id: "panel:unavailable",
        source: "panel",
        missing: true,
        error: "panel is unreachable",
      }),
      config({
        config_id: "panel:ok",
        source: "panel",
        platforms: [{ id: "ios", label: "iOS", apps: [app()] }],
      }),
    ]);

    expect(problems).toEqual([
      {
        config_id: "local:file",
        source: "file",
        missing: true,
        error: "local config is broken",
      },
      {
        config_id: "panel:unavailable",
        source: "panel",
        missing: true,
        error: "panel is unreachable",
      },
    ]);
  });

  it("never marks galleries orphaned when catalog completeness is unknown", () => {
    expect(orphanState(true, false)).toBe("unknown");
    expect(orphanState(false, false)).toBe("unknown");
    expect(orphanState(true, true)).toBe("orphaned");
    expect(orphanState(false, true)).toBe("known");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import { createUiChrome } from "./uiChrome.js";
import { resetShellState, shellState } from "./shellState.svelte";
type TestOverrides = { [key: string]: Record<string, unknown> | undefined };

function installWindowTimers() {
  vi.stubGlobal("window", {
    clearTimeout,
    setTimeout,
  });
}

function makeChrome(overrides: TestOverrides = {}) {
  const state = {
    currentLang: "ru",
  };
  resetShellState();
  const deps = {
    getCurrentLang: () => state.currentLang,
    normalizeLangCode: (value: unknown) =>
      String(value || "")
        .trim()
        .toLowerCase(),
    ...overrides.deps,
  };
  return { actions: createUiChrome(deps), deps, state };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("createUiChrome", () => {
  it("locks and unlocks background scrolling when a modal is active", () => {
    const body = { dataset: {} as Record<string, string> };
    vi.stubGlobal("document", { body });
    const { actions } = makeChrome();

    actions.syncBodyScrollLock(true);

    expect(body.dataset.scrollLocked).toBe("");
    expect(body.dataset.scrollLockCount).toBe("1");

    actions.syncBodyScrollLock(false);

    expect(body.dataset.scrollLocked).toBeUndefined();
    expect(body.dataset.scrollLockCount).toBeUndefined();
  });

  it("leaves the lock in place while another overlay still holds it", () => {
    const body = { dataset: {} as Record<string, string> };
    vi.stubGlobal("document", { body });
    const first = makeChrome().actions;
    const second = makeChrome().actions;

    first.syncBodyScrollLock(true);
    second.syncBodyScrollLock(true);
    first.syncBodyScrollLock(false);

    expect(body.dataset.scrollLocked).toBe("");

    second.syncBodyScrollLock(false);

    expect(body.dataset.scrollLocked).toBeUndefined();
  });

  it("arms and clears the language click guard around menu transitions", () => {
    vi.useFakeTimers();
    installWindowTimers();
    const { actions } = makeChrome();

    actions.setLanguageMenuOpen(true);

    expect(shellState.languageMenuOpen).toBe(true);
    expect(shellState.languageClickGuard).toBe(true);
    expect(shellState.languageClickGuardArmed).toBe(false);

    vi.advanceTimersByTime(220);

    expect(shellState.languageClickGuardArmed).toBe(true);

    actions.setLanguageMenuOpen(false);

    expect(shellState.languageMenuOpen).toBe(false);
    expect(shellState.languageClickGuard).toBe(true);
    expect(shellState.languageClickGuardArmed).toBe(false);

    vi.advanceTimersByTime(260);

    expect(shellState.languageClickGuard).toBe(false);
  });

  it("normalizes and applies a changed guest language", () => {
    vi.useFakeTimers();
    installWindowTimers();
    const { actions, state } = makeChrome();

    actions.updateGuestLanguage(" EN ");

    expect(shellState.guestLanguage).toBe("en");

    state.currentLang = "en";
    actions.updateGuestLanguage("en");

    expect(shellState.guestLanguage).toBe("en");
  });
});

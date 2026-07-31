import { afterEach, describe, expect, it, vi } from "vitest";

import { isPageScrollLocked, lockPageScroll } from "./scrollLock.js";

function stubBody() {
  const body = { dataset: {} as Record<string, string> };
  vi.stubGlobal("document", { body });
  return body;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("lockPageScroll", () => {
  it("marks the document while at least one overlay holds the lock", () => {
    const body = stubBody();

    const release = lockPageScroll();

    expect(body.dataset.scrollLocked).toBe("");
    expect(isPageScrollLocked()).toBe(true);

    release();

    expect(body.dataset.scrollLocked).toBeUndefined();
    expect(isPageScrollLocked()).toBe(false);
  });

  it("keeps the lock until the last holder releases it", () => {
    const body = stubBody();

    const first = lockPageScroll();
    const second = lockPageScroll();
    first();

    expect(body.dataset.scrollLockCount).toBe("1");
    expect(body.dataset.scrollLocked).toBe("");

    second();

    expect(body.dataset.scrollLockCount).toBeUndefined();
  });

  it("ignores a release called twice, so a stacked overlay stays locked", () => {
    const body = stubBody();

    const first = lockPageScroll();
    lockPageScroll();
    first();
    first();

    expect(body.dataset.scrollLockCount).toBe("1");
    expect(isPageScrollLocked()).toBe(true);
  });

  it("survives a stray counter written by another bundle", () => {
    const body = stubBody();
    body.dataset.scrollLockCount = "not-a-number";

    const release = lockPageScroll();

    expect(body.dataset.scrollLockCount).toBe("1");

    release();

    expect(isPageScrollLocked()).toBe(false);
  });
});

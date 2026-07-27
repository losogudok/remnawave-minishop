/**
 * Ref-counted page scroll lock shared by every overlay.
 *
 * Freezing `document.body` is not enough: the admin shell scrolls an inner
 * `.admin-main` element, so an open dialog used to float above a background
 * that still scrolled — two live scrollers at once, which is most obvious on
 * touch devices. The lock therefore marks the document and lets CSS freeze the
 * document *and* every app-level scroller tagged `data-scroll-container`, so a
 * container that mounts while the lock is held is frozen too.
 *
 * The counter lives on `document.body` rather than in module state because the
 * mini app and the admin panel are separate bundles sharing one page: two
 * module instances would otherwise unlock each other's overlays.
 */

const LOCK_ATTRIBUTE = "scrollLocked";
const COUNT_ATTRIBUTE = "scrollLockCount";

function readLockCount(body: HTMLElement): number {
  const count = Number(body.dataset[COUNT_ATTRIBUTE] || "0");
  return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
}

function applyLockCount(body: HTMLElement, count: number): void {
  if (count > 0) {
    body.dataset[COUNT_ATTRIBUTE] = String(count);
    body.dataset[LOCK_ATTRIBUTE] = "";
    return;
  }
  delete body.dataset[COUNT_ATTRIBUTE];
  delete body.dataset[LOCK_ATTRIBUTE];
}

/**
 * Freeze background scrolling until the returned release function is called.
 * Releasing twice is a no-op, so a component may call it from both a cleanup
 * and an explicit close path.
 */
export function lockPageScroll(): () => void {
  if (typeof document === "undefined") return () => {};
  const { body } = document;
  applyLockCount(body, readLockCount(body) + 1);
  let released = false;
  return () => {
    if (released) return;
    released = true;
    applyLockCount(body, Math.max(0, readLockCount(body) - 1));
  };
}

/** True while at least one overlay holds the lock. */
export function isPageScrollLocked(): boolean {
  if (typeof document === "undefined") return false;
  return readLockCount(document.body) > 0;
}

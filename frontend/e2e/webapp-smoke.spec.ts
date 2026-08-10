import { test, expect, type ConsoleMessage, type Locator, type Page } from "@playwright/test";

// Deterministic mock-smoke for the Svelte webapp (docs-demo build, mockApi, no
// backend). This is the standing UI regression gate for webapp/admin
// navigation, dialogs, dialog tabs, disclosure panels, activation handoff, and
// console health.

const APP_URL = "/demo/runtime/app/";
const DESKTOP_VIEWPORT = { width: 1280, height: 900 };
const MOBILE_VIEWPORT = { width: 390, height: 900 };

const NAV_TABS = [
  { label: "Главная", urlPart: "/demo/runtime/home" },
  { label: "Бонусы", urlPart: "/demo/runtime/invite" },
  { label: "Устройства", urlPart: "/demo/runtime/devices" },
  { label: "Поддержка", urlPart: "/demo/runtime/support" },
  { label: "Настройки", urlPart: "/demo/runtime/settings" },
] as const;

const CORE_ADMIN_SECTION_IDS = [
  "stats",
  "users",
  "payments",
  "promos",
  "ads",
  "broadcast",
  "logs",
  "support",
  "tariffs",
  "appearance",
  "translations",
  "backups",
  "settings",
] as const;

// Environmental noise that is not an app regression (no real backend / Telegram
// SDK / network in the mock). Keep this list tight: it must not mask app bugs.
const IGNORED_ERROR_PATTERNS: RegExp[] = [/favicon/i, /telegram\.org/i];

function isIgnoredError(text: string): boolean {
  return IGNORED_ERROR_PATTERNS.some((re) => re.test(text));
}

function trackErrors(page: Page, phase: () => string): string[] {
  const errors: string[] = [];
  page.on("console", (msg: ConsoleMessage) => {
    const location = msg.location();
    const where = location.url ? ` at ${location.url}:${location.lineNumber}` : "";
    if (msg.type() === "error" && !isIgnoredError(msg.text())) {
      errors.push(`[${phase()}] console.error${where}: ${msg.text()}`);
    }
    if (msg.type() === "warning" && /derived_inert/.test(msg.text())) {
      errors.push(`[${phase()}] console.warning${where}: ${msg.text()}`);
    }
  });
  page.on("pageerror", (err: Error) => {
    if (!isIgnoredError(err.message)) errors.push(`[${phase()}] pageerror: ${err.message}`);
  });
  return errors;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function adminSectionButton(page: Page, id: string): Locator {
  return page.locator(`[data-admin-section="${id}"]`);
}

function webappAction(page: Page, id: string): Locator {
  return page.locator(`[data-webapp-action="${id}"]:visible`);
}

function activeAdminSection(page: Page, id: string): Locator {
  return page.locator(`.admin-section-stage[data-admin-active-section="${id}"]:not([inert])`);
}

/**
 * Horizontal position of the hover drop line, in CSS px inside the overlay.
 * The highlight is painted on a canvas (it clips uPlot's own path), so this
 * samples the axis strip under the plot, where only the dashed line to the
 * readout card is drawn — that line marks the active point.
 */
async function chartHighlightCentre(chart: Locator): Promise<number | null> {
  return chart.locator(".admin-chart-highlight").evaluate((element) => {
    const canvas = element as HTMLCanvasElement;
    const context = canvas.getContext("2d");
    if (!context || !canvas.width || !canvas.height) return null;
    const bandTop = Math.floor(canvas.height * 0.9);
    const bandHeight = canvas.height - bandTop;
    if (bandHeight <= 0) return null;
    const pixels = context.getImageData(0, bandTop, canvas.width, bandHeight).data;
    let total = 0;
    let weighted = 0;
    for (let y = 0; y < bandHeight; y += 1) {
      for (let x = 0; x < canvas.width; x += 1) {
        const alpha = pixels[(y * canvas.width + x) * 4 + 3];
        if (alpha > 40) {
          total += alpha;
          weighted += x * alpha;
        }
      }
    }
    if (!total) return null;
    const ratio = canvas.width / (canvas.getBoundingClientRect().width || canvas.width);
    return weighted / total / ratio;
  });
}

async function assertNoDuplicateIds(page: Page, phase: string): Promise<void> {
  const duplicates = await page.locator("[id]").evaluateAll((elements) => {
    const seen = new Map<string, number>();
    for (const element of elements) {
      const html = element as HTMLElement;
      if (html.closest("[inert]")) continue;
      const id = html.id;
      if (!id) continue;
      seen.set(id, (seen.get(id) ?? 0) + 1);
    }
    return Array.from(seen.entries())
      .filter(([, count]) => count > 1)
      .map(([id, count]) => `${id} (${count})`);
  });
  expect(duplicates, `${phase}: element ids must be unique`).toEqual([]);
}

async function assertInteractiveControlsNamed(page: Page, phase: string): Promise<void> {
  const violations = await page
    .locator(
      [
        "button",
        "a[href]",
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="radio"]',
        '[role="checkbox"]',
        '[role="menuitem"]',
      ].join(", ")
    )
    .evaluateAll((elements) => {
      const isVisible = (element: Element): boolean => {
        const html = element as HTMLElement;
        const style = window.getComputedStyle(html);
        const rect = html.getBoundingClientRect();
        return (
          !html.closest("[inert]") &&
          !html.closest('[aria-hidden="true"]') &&
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      const labelFromIds = (ids: string): string =>
        ids
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" ")
          .replace(/\s+/g, " ")
          .trim();
      const accessibleName = (element: Element): string => {
        const ariaLabel = element.getAttribute("aria-label")?.trim();
        if (ariaLabel) return ariaLabel;
        const labelledBy = element.getAttribute("aria-labelledby")?.trim();
        if (labelledBy) {
          const labelledText = labelFromIds(labelledBy);
          if (labelledText) return labelledText;
        }
        const title = element.getAttribute("title")?.trim();
        if (title) return title;
        return (element.textContent ?? "").replace(/\s+/g, " ").trim();
      };
      const describe = (element: Element): string => {
        const html = element as HTMLElement;
        const role = html.getAttribute("role");
        const id = html.id ? `#${html.id}` : "";
        const className = (html.getAttribute("class") ?? "")
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .join(".");
        return `${html.tagName.toLowerCase()}${id}${role ? `[role=${role}]` : ""}${className ? `.${className}` : ""}`;
      };
      return elements
        .filter(isVisible)
        .filter((element) => !accessibleName(element))
        .map(describe);
    });
  expect(violations, `${phase}: visible interactive controls must have an accessible name`).toEqual(
    []
  );
}

async function assertNoNestedInteractiveControls(page: Page, phase: string): Promise<void> {
  const violations = await page
    .locator(
      [
        "button",
        "a[href]",
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="radio"]',
        '[role="checkbox"]',
        '[role="switch"]',
      ].join(", ")
    )
    .evaluateAll((elements) => {
      const interactiveSelector = [
        "button",
        "a[href]",
        "input:not([type='hidden'])",
        "select",
        "textarea",
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="radio"]',
        '[role="checkbox"]',
        '[role="switch"]',
        '[role="menuitem"]',
      ].join(", ");
      const isVisible = (element: Element): boolean => {
        const html = element as HTMLElement;
        const style = window.getComputedStyle(html);
        const rect = html.getBoundingClientRect();
        return (
          !html.closest("[inert]") &&
          !html.closest('[aria-hidden="true"]') &&
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0
        );
      };
      const describe = (element: Element): string => {
        const html = element as HTMLElement;
        const role = html.getAttribute("role");
        const id = html.id ? `#${html.id}` : "";
        const className = (html.getAttribute("class") ?? "")
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .join(".");
        return `${html.tagName.toLowerCase()}${id}${role ? `[role=${role}]` : ""}${className ? `.${className}` : ""}`;
      };
      return elements.filter(isVisible).flatMap((element) => {
        const nested = Array.from(element.querySelectorAll(interactiveSelector)).filter(isVisible);
        return nested.length
          ? [`${describe(element)} contains ${nested.map(describe).slice(0, 3).join(", ")}`]
          : [];
      });
    });
  expect(violations, `${phase}: interactive controls must not contain nested controls`).toEqual([]);
}

async function assertImagesNamed(page: Page, phase: string): Promise<void> {
  const violations = await page.locator("img").evaluateAll((elements) =>
    elements
      .filter((element) => {
        const image = element as HTMLImageElement;
        const style = window.getComputedStyle(image);
        const rect = image.getBoundingClientRect();
        return (
          !image.closest("[inert]") &&
          !image.closest('[aria-hidden="true"]') &&
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0
        );
      })
      .filter((element) => {
        const image = element as HTMLImageElement;
        return (
          image.getAttribute("role") !== "presentation" &&
          image.getAttribute("aria-hidden") !== "true" &&
          !image.hasAttribute("alt") &&
          !image.hasAttribute("aria-label") &&
          !image.hasAttribute("aria-labelledby")
        );
      })
      .map((element) => {
        const image = element as HTMLImageElement;
        return `img${image.className ? `.${String(image.className).split(/\s+/).filter(Boolean).slice(0, 2).join(".")}` : ""}`;
      })
  );
  expect(violations, `${phase}: visible images must have alt text or be marked decorative`).toEqual(
    []
  );
}

async function assertFormFieldsNamed(page: Page, phase: string): Promise<void> {
  await assertNoDuplicateIds(page, phase);
  await assertInteractiveControlsNamed(page, phase);
  await assertNoNestedInteractiveControls(page, phase);
  await assertImagesNamed(page, phase);

  const violations = await page
    .locator('input:not([type="hidden"]), textarea, select')
    .evaluateAll((elements) =>
      elements
        .filter((element) => {
          const field = element as HTMLElement;
          const style = window.getComputedStyle(field);
          const rect = field.getBoundingClientRect();
          return (
            !field.closest("[inert]") &&
            style.display !== "none" &&
            style.visibility !== "hidden" &&
            rect.width > 0 &&
            rect.height > 0
          );
        })
        .filter((element) => {
          const field = element as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
          return !field.id && !field.name;
        })
        .map((element) => {
          const field = element as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
          const label = field.getAttribute("aria-label") || field.getAttribute("placeholder") || "";
          return `${field.tagName.toLowerCase()}${field.type ? `[type=${field.type}]` : ""}${label ? ` "${label}"` : ""}`;
        })
    );
  expect(violations, `${phase}: visible form fields must have id or name`).toEqual([]);
}

async function openAdminSection(page: Page, id: string): Promise<Locator> {
  const button = adminSectionButton(page, id);
  await expect(button, `admin section button: ${id}`).toBeVisible();
  await button.click();
  await expect(page).toHaveURL(new RegExp(`/demo/runtime/admin/${escapeRegExp(id)}(?:$|[/?#])`));
  const stage = activeAdminSection(page, id);
  await expect(stage, `active admin section: ${id}`).toBeVisible();
  await assertFormFieldsNamed(page, `admin-section:${id}`);
  return stage;
}

async function closeDialog(card: Locator): Promise<void> {
  await card.locator(".dialog-head button").click();
  await expect(card).toBeHidden();
}

async function clickFirstVisibleEnabled(locator: Locator): Promise<boolean> {
  const count = await locator.count();
  for (let index = 0; index < count; index += 1) {
    const target = locator.nth(index);
    if (!(await target.isVisible())) continue;
    if (await target.isDisabled()) continue;
    await target.scrollIntoViewIfNeeded();
    await expect(target).toBeEnabled();
    await target.click();
    return true;
  }
  return false;
}

async function clickCardBody(page: Page, card: Locator, phase: string): Promise<void> {
  await card.scrollIntoViewIfNeeded();
  const box = await card.boundingBox();
  expect(box, `${phase}: card must have a clickable box`).not.toBeNull();
  if (!box) return;
  await page.mouse.click(box.x + Math.min(24, box.width / 2), box.y + Math.min(24, box.height / 2));
}

async function swipeUp(page: Page, target: Locator, phase: string): Promise<void> {
  const box = await target.boundingBox();
  const viewport = page.viewportSize();
  expect(box, `${phase}: swipe target must have a bounding box`).not.toBeNull();
  expect(viewport, `${phase}: viewport must be available`).not.toBeNull();
  if (!box || !viewport) return;

  const x = Math.round(Math.min(viewport.width - 20, Math.max(20, box.x + box.width / 2)));
  const startY = Math.round(
    Math.min(viewport.height - 60, Math.max(100, box.y + Math.min(box.height - 20, 420)))
  );
  const endY = Math.max(60, startY - 420);
  const session = await page.context().newCDPSession(page);
  try {
    await session.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y: startY }],
    });
    for (let y = startY - 40; y >= endY; y -= 40) {
      await session.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x, y }],
      });
      await page.waitForTimeout(20);
    }
    await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  } finally {
    await session.detach();
  }
}

async function assertUserTicketScrolling(page: Page, nav: Locator): Promise<void> {
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await nav.getByRole("button", { name: "Поддержка", exact: true }).click();
  await page.locator(".ticket-card").first().click();

  const messageViewport = page.locator(
    ".support-ticket-screen .support-message-scroll > .scroll-area__viewport"
  );
  const composer = page.locator(".support-ticket-screen .ticket-composer");
  await expect(composer).toBeVisible();
  const readReceipt = page
    .locator('.support-ticket-screen .ticket-message-receipt[title="Прочитано"]')
    .first();
  await expect(readReceipt).toBeVisible();
  await expect(readReceipt.locator(".lucide-check-check")).toBeVisible();
  const composerInput = composer.locator(".rt-surface .ProseMirror");
  await composerInput.click();
  await composerInput.pressSequentially("Проверка статуса доставки");
  await composer.locator(".ticket-composer-send").click();
  const sentReceipt = page
    .locator('.support-ticket-screen .ticket-message-receipt[title="Отправлено"]')
    .last();
  await expect(sentReceipt).toBeVisible();
  await expect(sentReceipt.locator(".lucide-check")).toBeVisible();
  await page.waitForTimeout(220);
  await expect
    .poll(() => messageViewport.evaluate((element) => element.scrollHeight - element.clientHeight))
    .toBeGreaterThan(0);

  await messageViewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await messageViewport.hover();
  await page.mouse.wheel(0, 480);
  await expect
    .poll(() => messageViewport.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);

  await page.setViewportSize(MOBILE_VIEWPORT);
  await messageViewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await swipeUp(page, messageViewport, "webapp-support:mobile-scroll");
  await expect
    .poll(() => messageViewport.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  await expect(composer).toBeInViewport();

  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.locator(".support-back-button").click();
  await expect(page.locator(".support-screen")).toBeVisible();
}

async function assertAdminTicketScrolling(page: Page, supportDialog: Locator): Promise<void> {
  const bodyViewport = supportDialog.locator(
    ":scope > .dialog-body-scroll > .scroll-area__viewport"
  );
  const messageScroll = supportDialog.locator(".support-admin-message-scroll");
  const messageViewport = messageScroll.locator(":scope > .scroll-area__viewport");
  const composer = supportDialog.locator(".support-admin-composer");

  await page.waitForTimeout(220);
  await expect
    .poll(() => bodyViewport.evaluate((element) => element.scrollHeight - element.clientHeight))
    .toBeGreaterThan(0);
  await bodyViewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await messageScroll.hover();
  await page.mouse.wheel(0, 480);
  await expect.poll(() => bodyViewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);

  await page.setViewportSize(MOBILE_VIEWPORT);
  await expect
    .poll(() => messageViewport.evaluate((element) => getComputedStyle(element).overflowY))
    .toBe("visible");
  await bodyViewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await swipeUp(page, messageScroll, "admin-support:mobile-scroll");
  await expect.poll(() => bodyViewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => page.locator(".dialog").evaluate((element) => element.scrollTop)).toBe(0);

  const modalCoversHeader = await page.evaluate(() => {
    const header = document.querySelector(".admin-header");
    if (!(header instanceof HTMLElement)) return false;
    const rect = header.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return Boolean(hit?.closest(".dialog"));
  });
  expect(modalCoversHeader, "admin-support: modal must stay above the section header").toBe(true);

  await bodyViewport.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(composer).toBeInViewport();
  await bodyViewport.evaluate((element) => {
    element.scrollTop = 0;
  });
  await page.setViewportSize(DESKTOP_VIEWPORT);
}

async function exerciseDialogTabs(
  card: Locator,
  expectedCount: number,
  setPhase: (value: string) => void,
  phasePrefix: string
): Promise<void> {
  const tabs = card.locator(".admin-tabs-trigger");
  await expect(tabs).toHaveCount(expectedCount);
  for (let index = 0; index < expectedCount; index += 1) {
    setPhase(`${phasePrefix}:tab:${index + 1}`);
    const tab = tabs.nth(index);
    await tab.scrollIntoViewIfNeeded();
    await tab.click();
    await expect
      .poll(async () => {
        const dataState = await tab.getAttribute("data-state");
        const ariaSelected = await tab.getAttribute("aria-selected");
        return dataState === "active" || ariaSelected === "true";
      })
      .toBe(true);
    await expect(card.locator(".admin-tabs-content:visible").first()).toBeVisible();
    await assertFormFieldsNamed(card.page(), `${phasePrefix}:tab:${index + 1}`);
  }
}

async function assertMobileExtendTariffSelectDoesNotTapThrough(
  page: Page,
  userDialog: Locator,
  phase: string
): Promise<void> {
  await page.setViewportSize(MOBILE_VIEWPORT);

  const actionsTab = userDialog.locator(".admin-tabs-trigger").nth(3);
  await actionsTab.click();
  const actionsPanel = userDialog.locator(".admin-actions-tab");
  await expect(actionsPanel).toBeVisible();

  await page.evaluate(() => {
    const trackedWindow = window as typeof window & {
      __adminResetTrialClickGuardAttached?: boolean;
      __adminResetTrialClicks?: number;
    };
    trackedWindow.__adminResetTrialClicks = 0;
    if (trackedWindow.__adminResetTrialClickGuardAttached) return;
    trackedWindow.__adminResetTrialClickGuardAttached = true;
    document.addEventListener(
      "click",
      (event) => {
        const target = event.target;
        if (target instanceof Element && target.closest(".admin-reset-trial-btn")) {
          trackedWindow.__adminResetTrialClicks = (trackedWindow.__adminResetTrialClicks ?? 0) + 1;
        }
      },
      true
    );
  });

  const resetTrialButton = actionsPanel.locator(".admin-reset-trial-btn");
  await expect(resetTrialButton).toBeVisible();
  await expect(resetTrialButton).toBeEnabled();

  const extendTariffTrigger = actionsPanel.locator(".admin-user-extend-tariff-select");
  await expect(extendTariffTrigger).toBeVisible();
  await expect(extendTariffTrigger).toBeEnabled();
  await extendTariffTrigger.click();

  const selectContent = page.locator(".admin-select-content:visible").last();
  await expect(selectContent).toBeVisible();

  const items = selectContent.locator(".admin-select-item");
  const itemCount = await items.count();
  expect(itemCount, `${phase}: extend tariff select should expose choices`).toBeGreaterThan(1);

  const targetItem = items.nth(Math.min(2, itemCount - 1));
  await expect(targetItem).toBeVisible();
  const targetLabel = (await targetItem.locator("span").first().innerText()).trim();
  const itemReceivesPointer = await targetItem.evaluate((item) => {
    const rect = item.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return Boolean(hit && (hit === item || item.contains(hit)));
  });
  expect(itemReceivesPointer, `${phase}: select option must receive pointer events`).toBe(true);

  await targetItem.click();
  await expect(extendTariffTrigger).toContainText(targetLabel);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const trackedWindow = window as typeof window & { __adminResetTrialClicks?: number };
        return trackedWindow.__adminResetTrialClicks ?? 0;
      })
    )
    .toBe(0);
}

async function openUserDetailFromCurrentSection(
  page: Page,
  setPhase: (value: string) => void,
  phasePrefix: string,
  options: { checkMobileTariffTapThrough?: boolean } = {}
): Promise<void> {
  const userDialog = page.locator(".dialog-card.admin-user-dialog");
  setPhase(`${phasePrefix}:user-card`);
  await expect(userDialog).toBeVisible();
  await assertFormFieldsNamed(page, `${phasePrefix}:user-card`);
  // Subscription, Activity, Logs, Actions, Message.
  await exerciseDialogTabs(userDialog, 5, setPhase, `${phasePrefix}:user-tabs`);

  setPhase(`${phasePrefix}:user-avatar`);
  if (
    await clickFirstVisibleEnabled(
      userDialog.locator(".admin-avatar-preview-trigger:not(:disabled)")
    )
  ) {
    const avatarDialog = page.locator(".dialog-card.admin-avatar-dialog");
    await expect(avatarDialog).toBeVisible();
    await assertFormFieldsNamed(page, `${phasePrefix}:user-avatar`);
    await closeDialog(avatarDialog);
  }

  setPhase(`${phasePrefix}:user-referrals`);
  if (
    await clickFirstVisibleEnabled(userDialog.locator('[data-admin-action="open-user-referrals"]'))
  ) {
    const referralsDialog = page.locator(".dialog-card.admin-user-referrals-dialog");
    await expect(referralsDialog).toBeVisible();
    await assertFormFieldsNamed(page, `${phasePrefix}:user-referrals`);
    await closeDialog(referralsDialog);
  }

  const actionsTab = userDialog.locator(".admin-tabs-trigger").nth(3);
  await actionsTab.click();
  const actionsPanel = userDialog.locator(".admin-actions-tab");
  await expect(actionsPanel).toBeVisible();
  await assertFormFieldsNamed(page, `${phasePrefix}:user-actions`);

  if (options.checkMobileTariffTapThrough) {
    setPhase(`${phasePrefix}:mobile-extend-tariff-select`);
    await assertMobileExtendTariffSelectDoesNotTapThrough(
      page,
      userDialog,
      `${phasePrefix}:mobile-extend-tariff-select`
    );
  }

  setPhase(`${phasePrefix}:message-composer`);
  // Messaging one customer moved out of the actions sheet into its own tab,
  // where the shared composer carries channels and buttons.
  // bits-ui renders the trigger's value as `data-value`, and the demo runs in
  // Russian, so neither the label text nor a `value` attribute identifies it.
  await userDialog.locator('.admin-tabs-trigger[data-value="message"]').first().click();
  const composerCard = userDialog.locator(".admin-user-action-sheet--message");
  await expect(composerCard).toBeVisible();
  await expect(composerCard.locator(".ProseMirror")).toBeVisible();
  await expect(composerCard.locator('[data-admin-action="send-user-message"]')).toBeDisabled();
  await assertFormFieldsNamed(page, `${phasePrefix}:message-composer`);
  await userDialog.locator('.admin-tabs-trigger[data-value="actions"]').first().click();
  await expect(actionsPanel).toBeVisible();

  setPhase(`${phasePrefix}:ban-confirm`);
  await actionsPanel.locator('[data-admin-action="request-user-ban-toggle"]').click();
  const banDialog = page.locator(".dialog-card.admin-user-ban-confirm-dialog");
  await expect(banDialog).toBeVisible();
  await assertFormFieldsNamed(page, `${phasePrefix}:ban-confirm`);
  await closeDialog(banDialog);

  setPhase(`${phasePrefix}:delete-confirm`);
  await actionsPanel.locator('[data-admin-action="request-user-delete"]').click();
  const deleteDialog = page.locator(".dialog-card.admin-user-delete-dialog");
  await expect(deleteDialog).toBeVisible();
  await assertFormFieldsNamed(page, `${phasePrefix}:delete-confirm`);
  await closeDialog(deleteDialog);

  await closeDialog(userDialog);
}

async function exerciseSettingsDisclosures(stage: Locator): Promise<void> {
  const sectionTriggers = stage.locator(
    ".admin-accordion > .admin-accordion-item > .admin-accordion-trigger"
  );
  const sectionCount = await sectionTriggers.count();
  for (let index = 0; index < sectionCount; index += 1) {
    const trigger = sectionTriggers.nth(index);
    if ((await trigger.getAttribute("data-state")) === "closed") {
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();
    }
  }

  const subsectionTriggers = stage.locator(".admin-settings-subsection-trigger");
  const subsectionCount = await subsectionTriggers.count();
  for (let index = 0; index < subsectionCount; index += 1) {
    const trigger = subsectionTriggers.nth(index);
    if ((await trigger.getAttribute("data-state")) === "closed") {
      await trigger.scrollIntoViewIfNeeded();
      await trigger.click();
    }
  }
}

async function exerciseWebappDialogs(
  page: Page,
  nav: Locator,
  setPhase: (value: string) => void
): Promise<void> {
  setPhase("webapp-payment-modal");
  await nav.getByRole("button", { name: "Главная", exact: true }).click();
  const paymentOpened = await clickFirstVisibleEnabled(webappAction(page, "open-payment"));
  expect(paymentOpened).toBe(true);
  const paymentDialog = page.locator(".dialog-card.webapp-payment-dialog");
  await expect(paymentDialog).toBeVisible();
  await assertFormFieldsNamed(page, "webapp-payment-modal");
  const tariffRows = paymentDialog.locator(".tariff-row");
  if ((await tariffRows.count()) > 0) {
    await tariffRows.first().click();
    const nextButton = paymentDialog.locator(".payment-submit-button").first();
    if (!(await nextButton.isDisabled())) {
      await nextButton.click();
      await expect(paymentDialog.locator(".period-card").first()).toBeVisible();
      await assertFormFieldsNamed(page, "webapp-payment-modal:checkout");
    }
  } else {
    await expect(paymentDialog.locator(".payment-dialog-body")).toBeVisible();
  }
  await closeDialog(paymentDialog);

  setPhase("webapp-tariff-change-modal");
  if (await clickFirstVisibleEnabled(webappAction(page, "open-tariff-change"))) {
    const changeDialog = page.locator(".dialog-card.webapp-tariff-change-dialog");
    await expect(changeDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-tariff-change-modal");
    const targetRows = changeDialog.locator(".tariff-action-card");
    if ((await targetRows.count()) > 0) {
      await targetRows.first().click();
    }
    const changeSubmit = changeDialog.locator(".payment-submit-button").first();
    if ((await changeSubmit.count()) > 0 && !(await changeSubmit.isDisabled())) {
      await changeSubmit.click();
      const confirmDialog = page.locator(".dialog-card.webapp-tariff-change-confirm-dialog");
      await expect(confirmDialog).toBeVisible();
      await assertFormFieldsNamed(page, "webapp-tariff-change-confirm-modal");
      await closeDialog(confirmDialog);
    }
    if (await changeDialog.isVisible()) {
      await closeDialog(changeDialog);
    }
  }

  setPhase("webapp-regular-topup-modal");
  if (await clickFirstVisibleEnabled(webappAction(page, "open-regular-topup"))) {
    const topupDialog = page.locator(".dialog-card.webapp-topup-dialog");
    await expect(topupDialog).toBeVisible();
    await expect(topupDialog.locator(".payment-dialog-body")).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-regular-topup-modal");
    await closeDialog(topupDialog);
  }

  setPhase("webapp-premium-topup-modal");
  if (await clickFirstVisibleEnabled(webappAction(page, "open-premium-topup"))) {
    const topupDialog = page.locator(".dialog-card.webapp-topup-dialog");
    await expect(topupDialog).toBeVisible();
    await expect(topupDialog.locator(".payment-dialog-body")).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-premium-topup-modal");
    await closeDialog(topupDialog);
  }

  setPhase("webapp-device-modals");
  await nav.getByRole("button", { name: "Устройства", exact: true }).click();
  if (await clickFirstVisibleEnabled(webappAction(page, "open-device-topup"))) {
    const deviceTopupDialog = page.locator(".dialog-card.webapp-device-topup-dialog");
    await expect(deviceTopupDialog).toBeVisible();
    await expect(deviceTopupDialog.locator(".payment-dialog-body")).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-device-topup-modal");
    await closeDialog(deviceTopupDialog);
  }
  if (await clickFirstVisibleEnabled(webappAction(page, "open-device-disconnect"))) {
    const deviceDisconnectDialog = page.locator(".dialog-card.webapp-device-disconnect-dialog");
    await expect(deviceDisconnectDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-device-disconnect-modal");
    await closeDialog(deviceDisconnectDialog);
  }

  setPhase("webapp-account-modals");
  await nav.getByRole("button", { name: "Настройки", exact: true }).click();
  if (await clickFirstVisibleEnabled(webappAction(page, "open-set-password"))) {
    const setPasswordDialog = page.locator(".dialog-card.webapp-set-password-dialog");
    await expect(setPasswordDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-set-password-modal");
    const inputs = setPasswordDialog.locator('input[type="password"]');
    await inputs.nth(0).fill("DemoPassword42");
    await inputs.nth(1).fill("DemoPassword42");
    await setPasswordDialog.locator(".payment-submit-button").click();
    const codeDialog = page.locator(".webapp-set-password-code-dialog");
    await expect(codeDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-set-password-code-modal");
    await codeDialog.locator("header button").click();
    await expect(codeDialog).toBeHidden();
  }
  if (await clickFirstVisibleEnabled(webappAction(page, "open-link-email"))) {
    const linkEmailDialog = page.locator(".dialog-card.webapp-link-email-dialog");
    await expect(linkEmailDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-link-email-modal");
    await linkEmailDialog.locator('input[type="email"]').fill("demo-e2e@example.test");
    await linkEmailDialog.locator(".payment-submit-button").click();
    const codeDialog = page.locator(".webapp-link-email-code-dialog");
    await expect(codeDialog).toBeVisible();
    await assertFormFieldsNamed(page, "webapp-link-email-code-modal");
    await codeDialog.locator("header button").click();
    await expect(codeDialog).toBeHidden();
  }
}

async function exerciseActivationSuccessHandoff(
  page: Page,
  setPhase: (value: string) => void
): Promise<void> {
  setPhase("webapp-activation-success-dialog");
  await page.evaluate(() => {
    localStorage.setItem(
      "rw_webapp_activation_handoff_v1",
      JSON.stringify({
        pending: {
          kind: "initial_subscription",
          source: "e2e",
          paymentId: "e2e",
          userKey: "",
          startedAt: Date.now(),
        },
        acknowledged: null,
      })
    );
  });
  await page.goto(APP_URL);
  const activationDialog = page.locator(".dialog-card.webapp-activation-success-dialog");
  await expect(activationDialog).toBeVisible();
  await assertFormFieldsNamed(page, "webapp-activation-success-dialog");
  await closeDialog(activationDialog);
  await expect(page.locator(".dialog-card:visible")).toHaveCount(0);
}

test("support ticket conversations scroll on desktop and mobile", async ({ page }) => {
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto(APP_URL);
  const nav = page.locator("nav.bottom-nav");
  await expect(nav).toBeVisible();

  await assertUserTicketScrolling(page, nav);

  await nav.locator(".rail-admin-entry").click();
  await adminSectionButton(page, "support").click();
  await page.locator(".support-inbox-row[data-ticket-id]").first().click();
  const supportDialog = page.locator(".dialog-card.support-ticket-dialog");
  await expect(supportDialog).toBeVisible();
  await assertAdminTicketScrolling(page, supportDialog);
});

test("device traffic bonuses stay legible on mobile", async ({ page }) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await page.goto(`${APP_URL}?mock=devices`);
  const nav = page.locator("nav.bottom-nav");
  await expect(nav).toBeVisible();
  await nav.getByRole("button", { name: "Устройства", exact: true }).click();

  const openTopup = webappAction(page, "open-device-topup");
  await expect(openTopup).toBeVisible();
  await openTopup.click();

  const dialog = page.locator(".dialog-card.webapp-device-topup-dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.locator(".hwid-traffic-bonus", {
      hasText: "Плюс 15 ГБ к месячному трафику",
    })
  ).toBeVisible();
  await expect(
    dialog.locator(".hwid-traffic-bonus", {
      hasText: "Плюс 45 ГБ к месячному трафику",
    })
  ).toBeVisible();
});

test("partner operations open their linked payment card", async ({ page }) => {
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto(
    "/demo/runtime/admin/partners/partner/PT-104?partner_admin_scenario=populated&theme_preview=dark"
  );

  await page.getByRole("tab", { name: "Операции", exact: true }).click();
  const activity = page.locator(".partners-detail-tabs");
  await expect(activity.locator("tbody")).toContainText("LG-903");

  const commission = activity
    .locator(".admin-entity-link.is-actionable")
    .filter({ hasText: "COM-184" })
    .first();
  await expect(commission).toBeVisible();
  await commission.click();

  const paymentDialog = page.locator(".dialog-card.admin-payment-dialog");
  await expect(paymentDialog).toBeVisible();
  await expect(paymentDialog).toContainText("Платёж #710024");
  await closeDialog(paymentDialog);
});

test("partner referral mode blocks referral controls without hiding promo codes", async ({
  page,
}) => {
  await page.goto("/demo/runtime/invite?mock=partner-referral-disabled&theme_preview=dark");

  const referralShell = page.locator(".referral-program-shell.is-disabled");
  await expect(referralShell).toBeVisible();
  await expect(referralShell.locator(".referral-program-disabled-overlay")).toContainText(
    "Для вас действует партнёрская программа"
  );
  await expect(referralShell.locator(".referral-program-content")).toHaveAttribute("inert", "");
  await expect(referralShell.locator(".referral-link-item")).toHaveCount(2);
  await expect(page.locator(".promo-code-input")).toBeEditable();
});

test("partner encryption diagnostic explains safe initial key setup", async ({ page }) => {
  await page.goto(
    "/demo/runtime/admin/settings/partner?partner_settings_scenario=missing_key&theme_preview=dark"
  );

  const diagnostic = page.locator(".partner-diagnostics .danger").filter({
    hasText: "Ключ шифрования реквизитов не задан",
  });
  await expect(diagnostic).toBeVisible();
  await expect(diagnostic.locator("[data-partner-encryption-command]")).toHaveText(
    "openssl rand -base64 32 | tr '+/' '-_'"
  );
  const diagnosticCards = page.locator(".partner-diagnostics > div");
  await expect(diagnosticCards).toHaveCount(2);
  const cardGeometry = await diagnosticCards.evaluateAll((cards) =>
    cards.map((card) => {
      const rect = card.getBoundingClientRect();
      return { bottom: rect.bottom, top: rect.top, width: rect.width };
    })
  );
  expect(Math.abs(cardGeometry[0].width - cardGeometry[1].width)).toBeLessThanOrEqual(1);
  expect(cardGeometry[1].top).toBeGreaterThan(cardGeometry[0].bottom);
  await expect(diagnostic).toContainText("PARTNER_REQUISITES_ENCRYPTION_KEY");
  await expect(diagnostic).toContainText("перезапустите backend и worker");
  await expect(diagnostic).toContainText("никогда не сохраняйте секрет в Git");

  await page.goto(
    "/demo/runtime/admin/settings/partner?partner_settings_scenario=program_on&theme_preview=dark"
  );
  await expect(page.locator("[data-partner-encryption-command]")).toHaveCount(0);
});

test("partner account stays compact, table-driven, and keeps the tour ring local", async ({
  page,
}) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await page.goto("/demo/runtime/partner?partner_scenario=active_populated&theme_preview=dark");

  const overview = page.locator(".partner-overview-card");
  await expect(overview.locator(".partner-overview-head")).toContainText("Партнёрский кабинет");
  await expect(overview.locator(".partner-link-item")).toHaveCount(2);
  await expect(overview.getByRole("button", { name: "Поделиться" })).toHaveCount(0);
  await expect(overview.getByRole("button", { name: "Открыть QR-код" })).toHaveCount(0);
  await expect(overview.locator(".partner-link-item code")).toHaveText([
    "https://t.me/minishop_bot?start=p_Q7m2pK8v4",
    "https://example.com/?start=p_Q7m2pK8v4",
  ]);
  await expect(page.locator(".partner-balance-card .partner-section-head")).toContainText(
    "Баланс и вывод"
  );
  const statistics = page.locator(".partner-stats-card");
  await expect(statistics.locator(".partner-section-head")).toContainText("Партнёрская статистика");
  await expect(statistics).not.toContainText("Детали партнёрской программы");
  await expect(page.locator(".partner-methods-section, .partner-methods")).toHaveCount(0);
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(20);
  const pagination = statistics.locator(".admin-pagination");
  await expect(pagination).toContainText("Страница 1 из 2");
  await expect(pagination).toContainText("Всего 21");

  await pagination.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(1);
  await expect(statistics.locator(".partner-data-table tbody tr").first()).toContainText(
    "Demo client 17"
  );
  await expect(pagination).toContainText("Страница 2 из 2");
  await pagination.getByRole("button", { name: "Назад", exact: true }).click();

  await page.getByRole("button", { name: "Вывести", exact: true }).click();
  const withdrawalDialog = page.locator(".dialog-card.partner-withdraw-dialog");
  await expect(withdrawalDialog).toBeVisible();
  await expect(withdrawalDialog.locator(".partner-method-options button")).toHaveCount(3);
  await closeDialog(withdrawalDialog);

  const firstLink = overview.locator(".partner-link-item").first();
  const linkField = firstLink.locator("code");
  const copyButton = firstLink.locator(".partner-copy-button");
  const [fieldBox, copyBox] = await Promise.all([
    linkField.boundingBox(),
    copyButton.boundingBox(),
  ]);
  expect(fieldBox).not.toBeNull();
  expect(copyBox).not.toBeNull();
  expect(Math.abs(copyBox!.y - fieldBox!.y)).toBeLessThan(2);
  expect(copyBox!.x).toBeGreaterThan(fieldBox!.x);
  expect(fieldBox!.x + fieldBox!.width).toBeLessThanOrEqual(copyBox!.x + 1);

  const firstTableRow = statistics.locator(".partner-data-table tbody tr").first();
  await expect(firstTableRow).toHaveCSS("display", "block");
  await statistics.getByRole("tab", { name: /Комиссии/ }).click();
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(20);
  await expect(statistics.locator(".partner-data-table thead th")).toHaveCount(5);
  const reversedCommission = statistics.locator(".partner-status-tooltip-trigger").first();
  await expect(reversedCommission).toContainText("Отменена");
  await reversedCommission.hover();
  await expect(page.locator(".partner-status-tooltip-content")).toContainText(
    "Начисление отменено, потому что платёж клиента был возвращён или аннулирован."
  );
  await pagination.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(1);
  await expect(statistics.locator(".partner-data-table tbody tr").first()).toContainText(
    "Demo client 17"
  );
  await pagination.getByRole("button", { name: "Назад", exact: true }).click();
  await statistics.getByRole("tab", { name: /Выводы/ }).click();
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(20);
  await expect(statistics.locator(".partner-data-table thead th")).toHaveCount(5);
  await pagination.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(statistics.locator(".partner-data-table tbody tr")).toHaveCount(1);
  await expect(statistics.locator(".partner-data-table tbody tr").first()).toContainText("WD-D17");
  await pagination.getByRole("button", { name: "Назад", exact: true }).click();

  await page.getByRole("button", { name: "Как это работает", exact: true }).click();
  const spotlight = page.locator(".partner-tour-spotlight.is-ready");
  await expect(spotlight).toBeVisible();
  const spotlightPaint = await spotlight.evaluate((element) => {
    const box = element.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    const ring = window.getComputedStyle(element, "::after");
    return {
      boxWidth: box.width,
      viewportWidth: window.innerWidth,
      shadow: style.boxShadow,
      transitionDuration: style.transitionDuration,
      ringBorderWidth: ring.borderTopWidth,
      ringOpacity: ring.opacity,
    };
  });
  expect(spotlightPaint.boxWidth).toBeLessThan(spotlightPaint.viewportWidth - 24);
  expect(spotlightPaint.shadow).toContain("9999px");
  expect(spotlightPaint.transitionDuration).toBe("0s");
  expect(spotlightPaint.ringBorderWidth).toBe("2px");
  expect(spotlightPaint.ringOpacity).toBe("1");

  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto("/demo/runtime/partner?partner_scenario=active_populated&theme_preview=dark");
  await expect(page.locator(".partner-copy-button").first()).toBeVisible();
  const desktopStatistics = page.locator(".partner-stats-card");
  await expect(desktopStatistics.locator(".partner-table-primary").first()).toContainText(
    "Alex M."
  );
  await desktopStatistics.getByRole("button", { name: "Оборот", exact: true }).click();
  await expect(desktopStatistics.locator(".partner-table-primary").first()).toContainText(
    "Demo client 17"
  );
  const desktopLayout = await page.evaluate(() => {
    const copyButton = document.querySelector<HTMLElement>(".partner-copy-button");
    return {
      viewportWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      copyRight: copyButton?.getBoundingClientRect().right ?? 0,
    };
  });
  expect(desktopLayout.scrollWidth).toBeLessThanOrEqual(desktopLayout.viewportWidth);
  expect(desktopLayout.copyRight).toBeLessThanOrEqual(desktopLayout.viewportWidth);
});

test("partner loading and empty chart states preserve their final geometry", async ({ page }) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await page.goto("/demo/runtime/partner?partner_scenario=loading&theme_preview=dark");

  const accountSkeleton = page.locator(".partner-skeleton");
  await expect(accountSkeleton.locator(".partner-skeleton-card")).toHaveCount(3);
  await expect(accountSkeleton.locator(".partner-skeleton-table-row")).toHaveCount(4);
  const mobileFit = await page.evaluate(() => {
    const card = document.querySelector<HTMLElement>(".partner-skeleton-card");
    const button = document.querySelector<HTMLElement>(
      ".partner-overview-head > .partner-skeleton-button"
    );
    return {
      buttonRight: button?.getBoundingClientRect().right ?? 0,
      cardRight: card?.getBoundingClientRect().right ?? 0,
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  expect(mobileFit.buttonRight).toBeLessThanOrEqual(mobileFit.cardRight);
  expect(mobileFit.scrollWidth).toBeLessThanOrEqual(mobileFit.viewportWidth);

  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto("/demo/runtime/admin/partners?partner_admin_scenario=loading&theme_preview=dark");
  const adminSkeleton = page.locator(".partners-skeleton");
  await expect(adminSkeleton.locator(".partners-kpi-card")).toHaveCount(8);
  await expect(
    adminSkeleton.locator(".partners-chart-block .admin-revenue-chart-body")
  ).toHaveCount(2);
  await expect(adminSkeleton.locator(".partners-preview-card")).toHaveCount(3);
  const skeletonKpiHeight = await adminSkeleton
    .locator(".partners-kpi-card")
    .first()
    .evaluate((element) => element.getBoundingClientRect().height);
  const skeletonChartHeight = await adminSkeleton
    .locator(".partners-chart-block .admin-revenue-svg-frame")
    .first()
    .evaluate((element) => element.getBoundingClientRect().height);

  await page.goto(
    "/demo/runtime/admin/partners?partner_admin_scenario=populated&theme_preview=dark"
  );
  const partnerDashboard = page.locator(".partners-admin-page");
  const finalKpiHeight = await partnerDashboard
    .locator(".partners-kpi-card")
    .first()
    .evaluate((element) => element.getBoundingClientRect().height);
  const finalChartHeight = await partnerDashboard
    .locator(".partners-chart-block .admin-revenue-svg-frame")
    .first()
    .evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(skeletonKpiHeight - finalKpiHeight)).toBeLessThanOrEqual(12);
  expect(Math.abs(skeletonChartHeight - finalChartHeight)).toBeLessThanOrEqual(1);

  await page.goto(
    "/demo/runtime/admin/partners?partner_admin_scenario=empty_charts&theme_preview=dark"
  );
  const emptyPartnerCharts = page.locator(".partners-chart-block .admin-chart-empty");
  await expect(emptyPartnerCharts).toHaveCount(2);
  await expect(emptyPartnerCharts).toHaveText(["Нет данных для графика", "Нет данных для графика"]);
  const emptyPartnerChartHeight = await emptyPartnerCharts
    .first()
    .locator("..")
    .evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(emptyPartnerChartHeight - finalChartHeight)).toBeLessThanOrEqual(1);

  await page.goto("/demo/runtime/admin/stats?stats_scenario=empty_revenue&theme_preview=dark");
  const emptyRevenueChart = page.locator(".admin-revenue-chart .admin-chart-empty");
  await expect(emptyRevenueChart).toHaveText("Нет данных для графика");
  await expect(page.locator(".admin-revenue-chart .admin-revenue-chart-body")).toHaveCount(0);
});

test("broadcast promo picker fills its mobile editor row", async ({ page }) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await page.goto("/demo/runtime/admin/broadcast?theme_preview=dark");

  await page.getByRole("button", { name: "Добавить кнопку", exact: true }).click();
  const row = page.locator(".admin-row-editor-broadcast").first();
  await row.getByRole("button", { name: "Кнопки", exact: true }).click();
  await page.getByRole("option", { name: "Промокод — в боте", exact: true }).click();

  const kindSelect = row.locator(".admin-select-trigger");
  const combo = row.locator(".admin-combobox");
  const input = row.getByRole("combobox", { name: "Выберите промокод", exact: true });
  const trigger = row.getByRole("button", { name: "Выберите промокод", exact: true });
  await input.click();
  const menu = page.locator(".admin-combobox-content");
  await expect(menu).toBeVisible();

  const rect = async (locator: Locator) =>
    locator.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return {
        bottom: bounds.bottom,
        left: bounds.left,
        right: bounds.right,
        top: bounds.top,
        width: bounds.width,
      };
    });
  const [kindRect, comboRect, inputRect, triggerRect, menuRect] = await Promise.all([
    rect(kindSelect),
    rect(combo),
    rect(input),
    rect(trigger),
    rect(menu),
  ]);

  expect(Math.abs(comboRect.width - kindRect.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(inputRect.width - comboRect.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(menuRect.width - comboRect.width)).toBeLessThanOrEqual(1);
  expect(triggerRect.left).toBeGreaterThanOrEqual(inputRect.left);
  expect(triggerRect.right).toBeLessThanOrEqual(inputRect.right);
  expect(triggerRect.top).toBeGreaterThanOrEqual(inputRect.top);
  expect(triggerRect.bottom).toBeLessThanOrEqual(inputRect.bottom);
});

test("webapp and admin sections, dialogs, tabs stay interactive without console errors", async ({
  page,
}) => {
  let phase = "boot";
  const setPhase = (value: string) => {
    phase = value;
  };
  const errors = trackErrors(page, () => phase);

  setPhase("boot");
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await page.goto(APP_URL);
  const nav = page.locator("nav.bottom-nav");
  await expect(nav).toBeVisible();
  await expect(page.getByRole("button", { name: "Сменить тариф" })).toBeVisible();
  await assertFormFieldsNamed(page, "boot");

  setPhase("bottom-nav");
  for (const tab of NAV_TABS) {
    const button = nav.getByRole("button", { name: tab.label, exact: true });
    await button.click();
    await expect(page).toHaveURL(new RegExp(escapeRegExp(tab.urlPart)));
    await expect(button).toHaveClass(/active/);
    await assertFormFieldsNamed(page, `bottom-nav:${tab.urlPart}`);
  }

  await exerciseWebappDialogs(page, nav, setPhase);

  setPhase("admin-entry");
  await nav.getByRole("button", { name: "Админ-панель", exact: true }).click();
  await expect(page).toHaveURL(/\/demo\/runtime\/admin\/stats/);
  const adminSidebar = page.locator("aside.admin-sidebar");
  await expect(adminSidebar).toBeVisible();

  setPhase("admin-dashboard:panel-version");
  const panelVersionTrigger = activeAdminSection(page, "stats").locator(
    ".admin-panel-version-trigger"
  );
  await expect(panelVersionTrigger).toBeVisible();
  await expect(panelVersionTrigger).toContainText("v3.2.1");
  await panelVersionTrigger.click();
  const panelVersionPopover = page.locator(".admin-panel-version-popover");
  await expect(panelVersionPopover).toBeVisible();
  await expect(panelVersionPopover).toContainText("Точная версия проверена");
  await expect(panelVersionPopover).toContainText("Проверенные версии");
  await page.keyboard.press("Escape");
  await expect(panelVersionPopover).toBeHidden();

  setPhase("admin-dashboard:user-filter-link");
  const paidUsersCounter = activeAdminSection(page, "stats").locator(
    '[data-admin-user-filter="paid"]'
  );
  await expect(paidUsersCounter).toBeVisible();
  await paidUsersCounter.click();
  await expect(page).toHaveURL(/\/demo\/runtime\/admin\/users\?users_filter=paid$/);
  await expect(activeAdminSection(page, "users")).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/demo\/runtime\/admin\/stats$/);
  await expect(activeAdminSection(page, "stats")).toBeVisible();

  setPhase("admin-dashboard:revenue-chart-hover");
  const revenueChart = activeAdminSection(page, "stats").locator(".admin-revenue-chart-body");
  const revenueChartOver = revenueChart.locator(".u-over");
  await revenueChart.scrollIntoViewIfNeeded();
  await expect(revenueChartOver).toBeVisible();
  const revenueChartOverBox = await revenueChartOver.boundingBox();
  if (!revenueChartOverBox) throw new Error("revenue chart hover surface has no bounding box");
  await revenueChartOver.hover({
    position: { x: revenueChartOverBox.width / 2, y: revenueChartOverBox.height / 2 },
  });
  await expect(revenueChart.locator(".admin-chart-tooltip.is-visible")).toBeVisible();
  await expect(revenueChart.locator(".admin-chart-highlight.is-visible")).toBeVisible();
  const firstTooltipBox = await revenueChart
    .locator(".admin-chart-tooltip.is-visible")
    .boundingBox();
  const firstHighlightCentre = await chartHighlightCentre(revenueChart);
  await revenueChartOver.hover({
    position: { x: revenueChartOverBox.width - 2, y: revenueChartOverBox.height / 2 },
  });
  await page.waitForTimeout(400);
  const secondHighlightCentre = await chartHighlightCentre(revenueChart);
  expect(secondHighlightCentre).not.toBeNull();
  expect(Math.abs((secondHighlightCentre ?? 0) - (firstHighlightCentre ?? 0))).toBeGreaterThan(20);
  const revenueWrapBox = await revenueChart.locator(".admin-revenue-uplot-wrap").boundingBox();
  const revenueTooltipBox = await revenueChart
    .locator(".admin-chart-tooltip.is-visible")
    .boundingBox();
  if (!revenueWrapBox || !revenueTooltipBox) {
    throw new Error("revenue chart tooltip has no bounding box");
  }
  if (!firstTooltipBox) throw new Error("revenue chart tooltip has no initial bounding box");
  const revenueTooltipLaneBox = await revenueChart
    .locator(".admin-chart-tooltip-lane")
    .boundingBox();
  const revenueHighlightBox = await revenueChart
    .locator(".admin-chart-highlight.is-visible")
    .boundingBox();
  // The readout's notch comes from the shared `Plate` component.
  const revenueTooltipArrowBox = await revenueChart
    .locator(".admin-chart-tooltip .ui-plate-arrow")
    .boundingBox();
  if (
    !revenueTooltipLaneBox ||
    !revenueHighlightBox ||
    !revenueTooltipArrowBox ||
    secondHighlightCentre === null
  ) {
    throw new Error("revenue chart overlay geometry is unavailable");
  }
  const highlightDotX = revenueHighlightBox.x + secondHighlightCentre;
  const tooltipArrowX = revenueTooltipArrowBox.x + revenueTooltipArrowBox.width / 2;
  expect(Math.abs(revenueTooltipBox.y - firstTooltipBox.y)).toBeLessThanOrEqual(1);
  expect(revenueTooltipLaneBox.y).toBeGreaterThanOrEqual(revenueWrapBox.y + revenueWrapBox.height);
  expect(Math.abs(revenueHighlightBox.width - revenueWrapBox.width)).toBeLessThanOrEqual(1);
  expect(Math.abs(revenueHighlightBox.height - revenueWrapBox.height)).toBeLessThanOrEqual(1);
  // The card is clamped at the extreme right; its arrow keeps a safe inset
  // from the rounded corner while staying visually attached to the point.
  expect(Math.abs(tooltipArrowX - highlightDotX)).toBeLessThanOrEqual(12);
  expect(
    revenueWrapBox.x + revenueWrapBox.width - revenueTooltipBox.x - revenueTooltipBox.width
  ).toBeGreaterThanOrEqual(7);

  setPhase("admin-section-registry");
  for (const id of CORE_ADMIN_SECTION_IDS) {
    await expect(adminSectionButton(page, id), `core admin section exists: ${id}`).toBeVisible();
  }

  for (const id of CORE_ADMIN_SECTION_IDS) {
    setPhase(`admin-section:${id}`);
    await openAdminSection(page, id);
  }

  setPhase("admin-broadcast:shortcode-picker");
  await openAdminSection(page, "broadcast");
  const shortcodeToggle = page.locator("[data-rt-shortcodes-toggle]");
  await expect(shortcodeToggle).toBeVisible();
  await shortcodeToggle.click();
  const shortcodeList = page.locator(".rt-menu-list");
  await expect(shortcodeList).toBeVisible();
  await expect(shortcodeList.locator(".rt-menu-scroll .scroll-area__viewport")).toBeVisible();
  await shortcodeList.locator(".rt-menu-item").first().click();
  await expect(page.locator(".rt-surface .rt-chip").first()).toBeVisible();
  await page.locator("[data-rt-source-toggle]").click();
  const broadcastSource = page.locator("textarea.rt-source");
  await expect(broadcastSource).toBeVisible();
  await broadcastSource.evaluate((element) => {
    const textarea = element as HTMLTextAreaElement;
    textarea.focus();
    textarea.setSelectionRange(0, textarea.value.length);
  });
  await page.locator('[data-rt-format="bold"]').click();
  await expect(broadcastSource).toHaveValue("<b>{first_name}</b>");
  await broadcastSource.evaluate((element) => {
    const textarea = element as HTMLTextAreaElement;
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
  });
  await page.locator('[data-rt-format="link"]').click();
  await expect(broadcastSource).toHaveValue('<b>{first_name}</b><a href="https://">https://</a>');
  await page.locator("[data-rt-source-toggle]").click();
  await expect(page.locator(".rt-surface .rt-chip").first()).toBeVisible();

  setPhase("admin-users:filter-dialog");
  await openAdminSection(page, "users");
  await page.setViewportSize(MOBILE_VIEWPORT);
  await expect(page.locator(".admin-users-filter-toggle")).toBeVisible();
  await page.locator(".admin-users-filter-toggle").click();
  const usersFilterDialog = page.locator(".dialog-card.admin-users-filter-dialog");
  await expect(usersFilterDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-users:filter-dialog");
  await closeDialog(usersFilterDialog);
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await expect(adminSidebar).toBeVisible();

  setPhase("admin-users:row-card");
  await page.locator("tr[data-user-id]").first().click();
  await openUserDetailFromCurrentSection(page, setPhase, "admin-users", {
    checkMobileTariffTapThrough: true,
  });
  await page.setViewportSize(DESKTOP_VIEWPORT);

  setPhase("admin-payments:payment-dialog");
  await openAdminSection(page, "payments");
  await page.locator(".admin-payment-id-btn").first().click();
  const paymentDialog = page.locator(".dialog-card.admin-payment-dialog");
  await expect(paymentDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-payments:payment-dialog");
  await closeDialog(paymentDialog);

  setPhase("admin-payments:user-card");
  await page.locator(".admin-payments-user-btn").first().click();
  await openUserDetailFromCurrentSection(page, setPhase, "admin-payments");

  setPhase("admin-codes:create-dialog");
  await openAdminSection(page, "promos");
  await page.locator('[data-admin-action="create-code"]').click();
  const createCodeDialog = page.locator(
    ".dialog-card.admin-promo-dialog:not(.admin-promo-edit-dialog)"
  );
  await expect(createCodeDialog).toBeVisible();
  await expect(createCodeDialog.locator(".admin-promo-effect-row")).toHaveCount(6);
  await assertFormFieldsNamed(page, "admin-codes:create-dialog");
  await closeDialog(createCodeDialog);

  setPhase("admin-codes:editor-dialog");
  await page.locator('[data-admin-action="open-code-settings"]').first().click();
  const codeEditorDialog = page.locator(".dialog-card.admin-promo-edit-dialog");
  await expect(codeEditorDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-codes:editor-dialog");
  await exerciseDialogTabs(codeEditorDialog, 2, setPhase, "admin-codes:tabs");
  await expect(codeEditorDialog.locator(".admin-promo-activations-tab")).toBeVisible();
  await assertFormFieldsNamed(page, "admin-codes:activations-tab");

  setPhase("admin-codes:activation-user-card");
  if (await clickFirstVisibleEnabled(codeEditorDialog.locator(".admin-promos-user-btn"))) {
    await openUserDetailFromCurrentSection(page, setPhase, "admin-codes");
  }
  await closeDialog(codeEditorDialog);

  setPhase("admin-ads:create-dialog");
  await openAdminSection(page, "ads");
  await page.locator('[data-admin-action="create-ad"]').click();
  const adDialog = page.locator(".dialog-card.admin-ad-dialog");
  await expect(adDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-ads:create-dialog");
  await closeDialog(adDialog);

  setPhase("admin-support:ticket-dialog");
  await openAdminSection(page, "support");
  await page.locator(".support-inbox-row[data-ticket-id]").first().click();
  const supportDialog = page.locator(".dialog-card.support-ticket-dialog");
  await expect(supportDialog).toBeVisible();
  await expect(supportDialog.locator(".support-admin-composer")).toBeVisible();
  await assertFormFieldsNamed(page, "admin-support:ticket-dialog");

  setPhase("admin-support:user-card");
  if (
    await clickFirstVisibleEnabled(
      supportDialog.locator('[data-admin-action="open-support-user-card"]')
    )
  ) {
    await openUserDetailFromCurrentSection(page, setPhase, "admin-support");
  }
  await closeDialog(supportDialog);

  setPhase("admin-tariffs:create-dialog");
  await openAdminSection(page, "tariffs");
  await page.locator('[data-admin-action="create-tariff"]').click();
  const tariffDialog = page.locator(".dialog-card.admin-tariff-dialog");
  await expect(tariffDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-tariffs:create-dialog");
  await exerciseDialogTabs(tariffDialog, 5, setPhase, "admin-tariffs:create-tabs");
  await closeDialog(tariffDialog);

  setPhase("admin-tariffs:edit-dialog");
  await page.locator('[data-admin-action="open-tariff-editor"]').first().click();
  await expect(tariffDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-tariffs:edit-dialog");
  await exerciseDialogTabs(tariffDialog, 5, setPhase, "admin-tariffs:edit-tabs");

  setPhase("admin-tariffs:edit-save");
  await tariffDialog.getByRole("tab").nth(0).click();
  await tariffDialog.locator('input[placeholder="100"]:visible').fill("750");
  await tariffDialog.getByRole("tab").nth(1).click();
  await tariffDialog.locator('input[placeholder="299"]:visible').first().fill("250");
  await tariffDialog.locator(".admin-dialog-actions").getByRole("button").last().click();
  await expect(tariffDialog).toBeHidden();
  await expect(page.locator(".admin-tariff-card").first()).toContainText("750 GB");

  setPhase("admin-tariffs:delete-dialog");
  await page.locator('[data-admin-action="open-tariff-delete"]').first().click();
  const tariffDeleteDialog = page.locator(".dialog-card.admin-tariff-delete-dialog");
  await expect(tariffDeleteDialog).toBeVisible();
  await assertFormFieldsNamed(page, "admin-tariffs:delete-dialog");
  await closeDialog(tariffDeleteDialog);

  setPhase("admin-appearance:panels");
  const appearanceStage = await openAdminSection(page, "appearance");
  await expect(appearanceStage.locator(".appearance-stack")).toBeVisible();
  await expect(appearanceStage.locator(".appearance-logo-grid").first()).toBeVisible();
  await expect(appearanceStage.locator(".appearance-theme-section").first()).toBeVisible();
  await assertFormFieldsNamed(page, "admin-appearance:panels");

  setPhase("admin-appearance:theme-card-select");
  const inactiveThemeCard = appearanceStage.locator(".admin-theme-card:not(.is-current)").first();
  await expect(inactiveThemeCard).toBeVisible();
  const inactiveThemeKey = await inactiveThemeCard.getAttribute("data-theme-key");
  expect(inactiveThemeKey, "admin-appearance:theme-card-select: theme key").toBeTruthy();
  await clickCardBody(page, inactiveThemeCard, "admin-appearance:theme-card-select");
  const selectedThemeCard = appearanceStage.locator(
    `.admin-theme-card[data-theme-key="${inactiveThemeKey}"]`
  );
  await expect(selectedThemeCard).toHaveClass(/is-current/);

  const defaultThemeCard = appearanceStage.locator(".default-theme-editor");
  await clickCardBody(page, defaultThemeCard, "admin-appearance:default-card-select");
  await expect(defaultThemeCard).toHaveClass(/is-current/);
  await assertFormFieldsNamed(page, "admin-appearance:theme-card-select");

  setPhase("admin-translations:panels");
  const translationsStage = await openAdminSection(page, "translations");
  await expect(translationsStage.locator(".admin-translations-toolbar")).toBeVisible();
  const audienceTabs = translationsStage.locator("[data-admin-translation-audience]");
  await expect
    .poll(async () => audienceTabs.count(), { timeout: 15_000 })
    .toBeGreaterThanOrEqual(3);
  const audienceCount = await audienceTabs.count();
  for (let index = 0; index < audienceCount; index += 1) {
    setPhase(`admin-translations:audience:${index + 1}`);
    await audienceTabs.nth(index).click();
    await expect(audienceTabs.nth(index)).toHaveClass(/is-active/);
  }
  await translationsStage.locator('[data-admin-translation-audience="all"]').click();
  const translationGroup = translationsStage.locator("[data-admin-translation-group]").first();
  await translationGroup.click();
  await expect(translationsStage.locator(".admin-translation-list").first()).toBeVisible();
  const localeToggle = translationsStage.locator("[data-admin-translation-locale]").first();
  await localeToggle.click();
  await expect(localeToggle).toHaveAttribute("aria-expanded", "true");
  await assertFormFieldsNamed(page, "admin-translations:locale-panel");

  setPhase("admin-settings:panels-and-icon-dialog");
  const settingsStage = await openAdminSection(page, "settings");
  await exerciseSettingsDisclosures(settingsStage);
  await assertFormFieldsNamed(page, "admin-settings:panels");
  const iconPickerTrigger = settingsStage.locator(".admin-icon-picker-trigger").first();
  if (await clickFirstVisibleEnabled(iconPickerTrigger)) {
    const iconPickerDialog = page.locator(".dialog-card.admin-icon-picker-dialog");
    await expect(iconPickerDialog).toBeVisible();
    await assertFormFieldsNamed(page, "admin-settings:icon-picker-dialog");
    await closeDialog(iconPickerDialog);
  }

  setPhase("admin-dialog-cleanup");
  await expect(page.locator(".dialog-card:visible")).toHaveCount(0);

  await exerciseActivationSuccessHandoff(page, setPhase);

  setPhase("console-health");
  expect(errors).toEqual([]);
});

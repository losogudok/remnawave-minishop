import { describe, expect, it, vi } from "vitest";

import { createTelegramViewportBridge, type TelegramViewportWebApp } from "./telegramViewport.js";

function makeRoot() {
  const attributes = new Map<string, string>();
  return {
    attributes,
    removeAttribute: vi.fn((name: string) => attributes.delete(name)),
    setAttribute: vi.fn((name: string, value: string) => attributes.set(name, value)),
  };
}

function makeTelegram(isFullscreen = false) {
  const handlers = new Map<string, () => void>();
  const telegram: TelegramViewportWebApp = {
    isFullscreen,
    offEvent: vi.fn((eventType, handler) => {
      if (handlers.get(eventType) === handler) handlers.delete(eventType);
    }),
    onEvent: vi.fn((eventType, handler) => handlers.set(eventType, handler)),
  };
  return { handlers, telegram };
}

describe("createTelegramViewportBridge", () => {
  it("tracks the current Telegram fullscreen state", () => {
    const root = makeRoot();
    const { handlers, telegram } = makeTelegram();
    const bridge = createTelegramViewportBridge({ root });

    bridge.setTelegram(telegram);
    expect(root.attributes.has("data-telegram-fullscreen")).toBe(false);

    telegram.isFullscreen = true;
    handlers.get("fullscreenChanged")?.();
    expect(root.attributes.get("data-telegram-fullscreen")).toBe("true");

    telegram.isFullscreen = false;
    handlers.get("fullscreenChanged")?.();
    expect(root.attributes.has("data-telegram-fullscreen")).toBe(false);
  });

  it("moves the listener when the Telegram instance changes and cleans up", () => {
    const root = makeRoot();
    const first = makeTelegram(true);
    const second = makeTelegram(false);
    const bridge = createTelegramViewportBridge({ root });

    bridge.setTelegram(first.telegram);
    bridge.setTelegram(second.telegram);
    expect(first.telegram.offEvent).toHaveBeenCalledWith("fullscreenChanged", expect.any(Function));
    expect(root.attributes.has("data-telegram-fullscreen")).toBe(false);

    bridge.destroy();
    expect(second.telegram.offEvent).toHaveBeenCalledWith(
      "fullscreenChanged",
      expect.any(Function)
    );
    expect(root.attributes.has("data-telegram-fullscreen")).toBe(false);
  });
});

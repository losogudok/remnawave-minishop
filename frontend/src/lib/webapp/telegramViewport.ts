export type TelegramViewportWebApp = {
  isFullscreen?: boolean;
  onEvent?: (eventType: "fullscreenChanged", eventHandler: () => void) => void;
  offEvent?: (eventType: "fullscreenChanged", eventHandler: () => void) => void;
};

type AttributeHost = {
  removeAttribute(name: string): void;
  setAttribute(name: string, value: string): void;
};

const FULLSCREEN_ATTRIBUTE = "data-telegram-fullscreen";

export function createTelegramViewportBridge({
  root = typeof document === "undefined" ? null : document.documentElement,
}: {
  root?: AttributeHost | null;
} = {}) {
  let telegram: TelegramViewportWebApp | null = null;

  function syncFullscreenState() {
    if (!root) return;
    if (telegram?.isFullscreen === true) {
      root.setAttribute(FULLSCREEN_ATTRIBUTE, "true");
    } else {
      root.removeAttribute(FULLSCREEN_ATTRIBUTE);
    }
  }

  function setTelegram(next: TelegramViewportWebApp | null) {
    if (telegram === next) {
      syncFullscreenState();
      return;
    }
    telegram?.offEvent?.("fullscreenChanged", syncFullscreenState);
    telegram = next;
    telegram?.onEvent?.("fullscreenChanged", syncFullscreenState);
    syncFullscreenState();
  }

  function destroy() {
    telegram?.offEvent?.("fullscreenChanged", syncFullscreenState);
    telegram = null;
    root?.removeAttribute(FULLSCREEN_ATTRIBUTE);
  }

  return { destroy, setTelegram, syncFullscreenState };
}

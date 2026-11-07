import { describe, expect, it } from "vitest";

import {
  buildPushPreview,
  plaintextForPushPreview,
  pushPreviewClickTarget,
  pushPreviewHasUndeliveredButtons,
  webappTitleFromSettingsSections,
} from "./broadcastPushPreview";
import { selectedPushPreviewChannel, type BroadcastChannelDescriptor } from "./broadcastChannels";

describe("broadcast push preview", () => {
  it("strips Telegram HTML and preserves paragraph breaks", () => {
    expect(plaintextForPushPreview("<b>Hello</b><br><i>World</i> &amp; friends")).toBe(
      "Hello\nWorld & friends"
    );
  });

  it("uses the first line as the title and the rest as the body", () => {
    expect(buildPushPreview("<b>Launch</b>\n\nBody <i>details</i>", "Minishop")).toMatchObject({
      title: "Launch",
      body: "Body details",
      titleLength: 6,
      bodyLength: 12,
      titleTruncated: false,
      bodyTruncated: false,
    });
  });

  it("falls back to the app title for an empty or overlong first line", () => {
    expect(buildPushPreview("", "Minishop")).toMatchObject({
      title: "Minishop",
      body: "",
    });
    expect(buildPushPreview("\nBody only", "Minishop")).toMatchObject({
      title: "Minishop",
      body: "Body only",
    });
    expect(buildPushPreview(`${"x".repeat(130)}\nBody`, "Minishop")).toMatchObject({
      title: "Minishop",
      body: `${"x".repeat(130)} Body`,
    });
  });

  it("uses the configured WEBAPP_TITLE as the fallback and counts it correctly", () => {
    const customTitle = "My Shop";
    expect(
      webappTitleFromSettingsSections(
        [{ fields: [{ key: "WEBAPP_TITLE", value: customTitle }] }],
        "Minishop"
      )
    ).toBe(customTitle);
    expect(
      webappTitleFromSettingsSections(
        [{ fields: [{ key: "WEBAPP_TITLE", default: "Default Shop" }] }],
        "Minishop"
      )
    ).toBe("Default Shop");
    expect(webappTitleFromSettingsSections([], "Minishop")).toBe("Minishop");

    expect(buildPushPreview("", customTitle)).toMatchObject({
      title: customTitle,
      titleLength: customTitle.length,
      body: "",
    });
    expect(buildPushPreview(`${"x".repeat(130)}\nBody`, customTitle)).toMatchObject({
      title: customTitle,
      titleLength: customTitle.length,
      body: `${"x".repeat(130)} Body`,
    });
  });

  it("truncates the preview to the service-worker title and body limits", () => {
    const preview = buildPushPreview(`Title\n${"y".repeat(300)}`, "Minishop");
    expect(preview.titleTruncated).toBe(false);
    expect(preview.bodyTruncated).toBe(true);
    expect(preview.bodyLength).toBe(300);
    expect(preview.body.endsWith("…")).toBe(true);
    expect([...preview.body].length).toBe(200);
  });

  it("uses the first URL button only when it is same-origin and safe", () => {
    expect(
      pushPreviewClickTarget(
        [{ kind: "url", url: "https://app.example.test/support?topic=billing#secret" }],
        "https://app.example.test"
      )
    ).toBe("/support?topic=billing");
    expect(
      pushPreviewClickTarget(
        [{ kind: "promo_bot", url: "https://t.me/bot" }],
        "https://app.example.test"
      )
    ).toBe("/");
    expect(
      pushPreviewClickTarget(
        [{ kind: "url", url: "https://evil.example/support" }],
        "https://app.example.test"
      )
    ).toBe("/");
    expect(
      pushPreviewClickTarget(
        [{ kind: "url", url: "https://app.example.test/s/sharetoken" }],
        "https://app.example.test"
      )
    ).toBe("/");
    expect(
      pushPreviewClickTarget(
        [{ kind: "url", url: "https://app.example.test/support?access_token=secret" }],
        "https://app.example.test"
      )
    ).toBe("/");
    expect(
      pushPreviewClickTarget(
        [
          { kind: "url", url: "https://app.example.test/devices" },
          { kind: "url", url: "https://app.example.test/support" },
        ],
        "https://app.example.test"
      )
    ).toBe("/devices");
  });

  it("only selects a push-preview channel while its channel is selected", () => {
    const push: BroadcastChannelDescriptor = {
      name: "push",
      labelKey: "broadcast_channel_push",
      fallbackLabel: "Web Push",
      order: 30,
      available: true,
      reason: "",
      supportsHtml: false,
      supportsSubject: false,
      supportsImage: false,
      supportsButtons: true,
      maxTextLength: 320,
      supportsPushPreview: true,
      destinationLevel: "endpoint",
    };
    const telegram: BroadcastChannelDescriptor = {
      ...push,
      name: "telegram",
      labelKey: "broadcast_channel_telegram",
      fallbackLabel: "Telegram",
      supportsHtml: true,
      supportsImage: true,
      supportsPushPreview: false,
      destinationLevel: "user",
    };
    expect(selectedPushPreviewChannel([telegram, push], ["telegram"])).toBeNull();
    expect(selectedPushPreviewChannel([telegram, push], ["push"])).toEqual(push);
  });

  it("updates the preview content when the composer text changes", () => {
    expect(buildPushPreview("First\nBody", "Minishop")).toMatchObject({
      title: "First",
      body: "Body",
    });
    expect(buildPushPreview("<b>Second</b>\nUpdated", "Minishop")).toMatchObject({
      title: "Second",
      body: "Updated",
    });
  });

  it("reports buttons that push cannot deliver", () => {
    expect(pushPreviewHasUndeliveredButtons([])).toBe(false);
    expect(pushPreviewHasUndeliveredButtons([{ kind: "url", url: "/support" }])).toBe(false);
    expect(
      pushPreviewHasUndeliveredButtons([
        { kind: "url", url: "/support" },
        { kind: "url", url: "/devices" },
      ])
    ).toBe(true);
    expect(pushPreviewHasUndeliveredButtons([{ kind: "promo_bot", url: "https://t.me/bot" }])).toBe(
      true
    );
  });
});

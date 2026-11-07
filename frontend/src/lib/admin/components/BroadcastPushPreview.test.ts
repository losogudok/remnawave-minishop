import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import BroadcastPushPreview from "./BroadcastPushPreview.svelte";

const at = (_key: string, params?: Record<string, unknown>, fallback?: string) => {
  if (!fallback) return _key;
  return Object.entries(params ?? {}).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    fallback
  );
};

function renderPreview(overrides: Record<string, unknown> = {}) {
  return render(BroadcastPushPreview, {
    props: {
      title: "Launch",
      body: "Body details",
      clickTarget: "/support",
      titleLength: 6,
      bodyLength: 12,
      titleTruncated: false,
      bodyTruncated: false,
      hasImage: false,
      hasUndeliveredButtons: false,
      at,
      ...overrides,
    },
  }).body;
}

describe("BroadcastPushPreview", () => {
  it("renders the live title, body, counts and click target", () => {
    const html = renderPreview();
    expect(html).toContain("Launch");
    expect(html).toContain("Body details");
    expect(html).toContain("Title 6/120");
    expect(html).toContain("Body 12/200");
    expect(html).toContain("Tap opens /support");
    expect(html).toContain("Push is plain text");
    expect(html).not.toContain("Web Push shortens");
  });

  it("explains truncation, image and button limits without promising OS display", () => {
    const html = renderPreview({
      titleTruncated: true,
      bodyTruncated: true,
      hasImage: true,
      hasUndeliveredButtons: true,
      clickTarget: "/",
    });
    expect(html).toContain("Web Push shortens the text");
    expect(html).toContain("Images are not delivered to push");
    expect(html).toContain("Additional buttons stay available");
    expect(html).toContain("does not confirm that the OS displayed it");
    expect(html).toContain("Tap opens /");
  });

  it("shows an empty-state hint instead of a fake notification", () => {
    const html = renderPreview({ title: "", body: "", titleLength: 0, bodyLength: 0 });
    expect(html).toContain("Enter text to preview the push notification");
    expect(html).not.toContain("Launch");
  });
});

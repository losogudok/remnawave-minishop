import { render } from "svelte/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { asBroadcastChannels } from "$lib/admin/stores/broadcastChannels";
import { createBroadcastStore, type BroadcastStore } from "$lib/admin/stores/broadcastStore.svelte";
import UserMessageComposerCard from "./UserMessageComposerCard.svelte";

const context = vi.hoisted(() => ({ store: null as BroadcastStore | null }));
vi.mock("$lib/admin/context", () => ({
  getBroadcastStore: () => context.store,
  getAdminApi: () => vi.fn(),
  getAdminApiBlob: () => vi.fn(),
}));

const at = (key: string, _params?: Record<string, unknown>, fallback?: string) => fallback || key;

beforeEach(() => {
  context.store = createBroadcastStore({ api: vi.fn(), onToast: vi.fn(), at });
});

describe("user message delivery", () => {
  it.each([true, false])("shows the discovered Web Push channel (available: %s)", (available) => {
    context.store!.updateField({
      broadcastChannels: asBroadcastChannels([
        {
          name: "push",
          label_key: "broadcast_channel_push",
          fallback_label: "Web Push",
          available,
          reason: available ? "" : "push_disabled",
          supports_push_preview: true,
        },
      ]),
    });
    const html = render(UserMessageComposerCard, { props: { at, userId: 42 } }).body;
    expect(html).toContain('id="user-message-channel-push"');
    expect(html).toContain("Web Push");
    const pushCheckbox = html.match(/<button[^>]*id="user-message-channel-push"[^>]*>/)?.[0];
    expect(pushCheckbox).toBeTruthy();
    expect(pushCheckbox?.includes("disabled")).toBe(!available);
  });
});

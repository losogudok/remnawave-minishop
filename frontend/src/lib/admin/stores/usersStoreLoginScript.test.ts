import { beforeEach, describe, expect, it, vi } from "vitest";
import { createUsersStore } from "./usersStore.js";
import { copyTextToClipboard } from "../../webapp/clipboard.js";

vi.mock("../../webapp/clipboard.js", () => ({ copyTextToClipboard: vi.fn() }));

function setup(response: unknown) {
  const api = vi.fn().mockResolvedValue(response);
  const onToast = vi.fn();
  const store = createUsersStore({
    api,
    onToast,
    at: (key, _params = {}, fallback = key) => fallback,
  });
  store.updateState({ openedUser: { user_id: 68 } });
  return { api, onToast, store };
}

beforeEach(() => vi.resetAllMocks());

describe("user login script", () => {
  it("copies the issued script without changing the admin session", async () => {
    vi.mocked(copyTextToClipboard).mockResolvedValue(true);
    const { api, store, onToast } = setup({
      ok: true,
      script: "private script",
      expires_in: 86400,
    });
    await store.copyUserLoginScript();
    expect(api).toHaveBeenCalledWith("/admin/users/68/login-script", { method: "POST" });
    expect(copyTextToClipboard).toHaveBeenCalledWith("private script");
    expect(onToast).toHaveBeenCalledWith(expect.stringContaining("24 hours"));
    expect(store.userActionBusy).toBe(false);
    expect(store.openedUser?.user_id).toBe(68);
  });
  it("does not expose credentials in a toast when copying fails", async () => {
    vi.mocked(copyTextToClipboard).mockResolvedValue(false);
    const { store, onToast } = setup({ ok: true, script: "private script", expires_in: 86400 });
    await store.copyUserLoginScript();
    expect(onToast).toHaveBeenCalledWith(expect.stringContaining("clipboard access"));
    expect(JSON.stringify(onToast.mock.calls)).not.toContain("private script");
  });
  it("does not copy a denied response", async () => {
    const { store } = setup({ ok: false, error: "forbidden" });
    await store.copyUserLoginScript();
    expect(copyTextToClipboard).not.toHaveBeenCalled();
    expect(store.userActionBusy).toBe(false);
  });
  it("ignores duplicate clicks while busy", async () => {
    const { store, api } = setup({ ok: true });
    store.updateState({ userActionBusy: true });
    await store.copyUserLoginScript();
    expect(api).not.toHaveBeenCalled();
  });
});

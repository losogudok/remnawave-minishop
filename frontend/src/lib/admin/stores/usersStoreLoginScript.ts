import { adminErrorMessage } from "../errors.js";
import { copyTextToClipboard } from "../../webapp/clipboard.js";
import { buildAdminUserActionPath, unwrap } from "../../webapp/publicApi";
import type { AdminApi, AdminStoreState, ToastFn, TranslateFn } from "./usersStoreState";

export function createUsersStoreLoginScriptAction({
  api,
  onToast,
  at,
  readStateSnapshot,
  applyState,
}: {
  api: AdminApi;
  onToast: ToastFn;
  at: TranslateFn;
  readStateSnapshot: () => AdminStoreState;
  applyState: (updater: (state: AdminStoreState) => AdminStoreState) => void;
}) {
  async function copyUserLoginScript() {
    const state = readStateSnapshot();
    if (!state.openedUser || state.userActionBusy) return;
    applyState((s) => ({ ...s, userActionBusy: true }));
    try {
      const response = await api(
        buildAdminUserActionPath(state.openedUser.user_id, "login-script"),
        { method: "POST" }
      );
      if (!response?.ok) {
        onToast(adminErrorMessage(response, at));
        return;
      }
      const payload = unwrap(response);
      const copied =
        "script" in payload &&
        typeof payload.script === "string" &&
        (await copyTextToClipboard(payload.script));
      onToast(
        copied
          ? at(
              "user_login_script_copied",
              {},
              "Script copied. Open the cabinet in incognito mode and paste it into the DevTools console. Session: 24 hours."
            )
          : at(
              "user_login_script_copy_failed",
              {},
              "Could not copy the script. Allow clipboard access and try again."
            )
      );
    } catch {
      onToast(at("user_login_script_failed", {}, "Could not create a login script."));
    } finally {
      applyState((s) => ({ ...s, userActionBusy: false }));
    }
  }
  return { copyUserLoginScript };
}

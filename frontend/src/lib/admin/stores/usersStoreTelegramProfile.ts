import { adminErrorMessage } from "../errors.js";
import { buildAdminUserActionPath } from "../../webapp/publicApi";
import type { AdminApi, AdminStoreState, ToastFn, TranslateFn } from "./usersStoreState";

export function createUsersStoreTelegramProfileAction({
  api,
  onToast,
  at,
  readStateSnapshot,
  applyState,
  invalidateUsersQueries,
  reportUserActionError,
}: {
  api: AdminApi;
  onToast: ToastFn;
  at: TranslateFn;
  readStateSnapshot: () => AdminStoreState;
  applyState: (updater: (state: AdminStoreState) => AdminStoreState) => void;
  invalidateUsersQueries: (userId?: number | string) => void;
  reportUserActionError: (error: unknown) => void;
}) {
  async function sendTelegramProfileLink() {
    const s = readStateSnapshot();
    if (!s.openedUser) return;
    applyState((st) => ({ ...st, userActionBusy: true }));
    try {
      const res = await api(
        buildAdminUserActionPath(s.openedUser.user_id, "telegram-profile-link"),
        {
          method: "POST",
        }
      );
      if (res?.ok) {
        invalidateUsersQueries(s.openedUser.user_id);
        onToast(at("user_tg_profile_link_sent", {}, "Link sent to Telegram"));
      } else {
        onToast(
          adminErrorMessage(res, at, at("user_tg_profile_link_failed", {}, "Failed to send link"))
        );
      }
    } catch (error) {
      reportUserActionError(error);
    } finally {
      applyState((st) => ({ ...st, userActionBusy: false }));
    }
  }

  return { sendTelegramProfileLink };
}

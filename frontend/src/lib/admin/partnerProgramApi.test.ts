import { describe, expect, it, vi } from "vitest";

import type { AdminApi } from "../../admin/adminStores.js";
import {
  DEFAULT_PARTNER_LIST_QUERY,
  loadPartnerDashboard,
  loadPartnerPage,
  PARTNER_LIST_PAGE_SIZE,
} from "./partnerProgramApi.js";

describe("partner program admin API", () => {
  it("requests the selected server-side page and sort order", async () => {
    const request = vi.fn().mockResolvedValue({
      partners: [
        {
          partner_id: 12,
          user_id: 34,
          display_label: "Partner",
          status: "active",
          commission_bps: 3000,
          clients_count: 7,
          balances: [],
        },
      ],
      total: 5781,
    });

    const result = await loadPartnerPage(request as unknown as AdminApi, "RUB", {
      page: 2,
      search: "  34  ",
      status: "active",
      sort: "clients_desc",
    });

    const path = String(request.mock.calls[0][0]);
    const query = new URLSearchParams(path.split("?")[1]);
    expect(query.get("limit")).toBe(String(PARTNER_LIST_PAGE_SIZE));
    expect(query.get("offset")).toBe(String(PARTNER_LIST_PAGE_SIZE * 2));
    expect(query.get("search")).toBe("34");
    expect(query.get("status")).toBe("active");
    expect(query.get("sort")).toBe("clients_desc");
    expect(result.total).toBe(5781);
    expect(result.partners[0].clients).toBe(7);
  });

  it("maps the current user identity for partner rows", async () => {
    const request = vi.fn().mockResolvedValue({
      partners: [
        {
          partner_id: 12,
          user_id: 34,
          display_label: "Alice Partner",
          username: "alice",
          avatar_url: "/api/admin/users/34/avatar?v=1",
          status: "active",
          commission_bps: 3000,
          clients_count: 7,
          balances: [],
        },
      ],
      total: 1,
    });

    const result = await loadPartnerPage(
      request as unknown as AdminApi,
      "RUB",
      DEFAULT_PARTNER_LIST_QUERY
    );

    expect(result.partners[0]).toMatchObject({
      userId: 34,
      name: "Alice Partner",
      handle: "@alice",
      avatarUrl: "/api/admin/users/34/avatar?v=1",
      clients: 7,
    });
  });

  it("shows partners with clients first by default", () => {
    expect(DEFAULT_PARTNER_LIST_QUERY.sort).toBe("clients_desc");
  });

  it("supports a bounded top-partner page", async () => {
    const request = vi.fn().mockResolvedValue({ partners: [], total: 0 });

    await loadPartnerPage(request as unknown as AdminApi, "RUB", {
      ...DEFAULT_PARTNER_LIST_QUERY,
      sort: "earned_desc",
      limit: 3,
    });

    const query = new URLSearchParams(String(request.mock.calls[0][0]).split("?")[1]);
    expect(query.get("limit")).toBe("3");
    expect(query.get("sort")).toBe("earned_desc");
  });

  it("loads the complete partner chart history", async () => {
    const request = vi.fn().mockResolvedValue({ metrics: {}, series: [], currency_scale: 2 });

    await loadPartnerDashboard(request as unknown as AdminApi, "RUB");

    expect(String(request.mock.calls[0][0])).toContain("days=all");
  });
});

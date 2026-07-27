import { describe, expect, it } from "vitest";

import {
  applyProductToTrafficRow,
  applySubscriptionToPeriodRows,
  checkPeriodRows,
  checkTrafficRows,
  normalizeTributeCatalog,
  productOptionLabel,
  subscriptionOptionLabel,
  type TributeCatalog,
  type TributeCatalogProduct,
  type TributeCatalogSubscription,
} from "./tributeCatalog";

const subscription: TributeCatalogSubscription = {
  subscription_id: 101,
  name: "Standard",
  currency: "rub",
  periods: [
    { period_id: 1001, period: "monthly", price: 299, months: 1 },
    { period_id: 1003, period: "quarterly", price: 799, months: 3 },
    { period_id: 1077, period: "weekly", price: 99, months: null },
  ],
};

const product: TributeCatalogProduct = {
  product_id: 501,
  name: "50 GB",
  type: "digital",
  status: "approved",
  price: 199,
  currency: "rub",
  link: "https://web.tribute.tg/p/501",
};

const catalog: TributeCatalog = { subscriptions: [subscription], products: [product] };

describe("normalizeTributeCatalog", () => {
  it("falls back to empty lists", () => {
    expect(normalizeTributeCatalog(null)).toEqual({ subscriptions: [], products: [] });
    expect(normalizeTributeCatalog({ subscriptions: "nope" })).toEqual({
      subscriptions: [],
      products: [],
    });
  });
});

describe("option labels", () => {
  it("keeps the numeric id visible", () => {
    expect(subscriptionOptionLabel(subscription)).toBe("Standard · #101");
    expect(subscriptionOptionLabel({ ...subscription, name: "" })).toBe("#101");
    expect(productOptionLabel(product)).toBe("50 GB · #501");
  });
});

describe("applySubscriptionToPeriodRows", () => {
  it("binds every period Tribute sells and leaves the link alone", () => {
    const rows = [
      { months: 1, rub: 299, tribute_link: "https://web.tribute.tg/s/11Pv" },
      { months: "3", rub: "799" },
    ];

    const result = applySubscriptionToPeriodRows(subscription, rows, "RUB");

    expect(result.updates).toEqual([
      { index: 0, values: { tribute_subscription_id: 101, tribute_period_id: 1001 } },
      { index: 1, values: { tribute_subscription_id: 101, tribute_period_id: 1003 } },
    ]);
    expect(result.issues).toEqual([]);
  });

  it("reports a period the subscription does not sell", () => {
    const result = applySubscriptionToPeriodRows(subscription, [{ months: 12, rub: 2990 }], "RUB");

    expect(result.updates).toEqual([]);
    expect(result.issues).toEqual([{ kind: "missing_period", months: 12 }]);
  });

  it("reports a price and a currency that drifted from Tribute", () => {
    const rows = [{ months: 1, rub: 250 }];

    const result = applySubscriptionToPeriodRows({ ...subscription, currency: "eur" }, rows, "RUB");

    expect(result.updates).toHaveLength(1);
    expect(result.issues).toEqual([
      { kind: "currency_mismatch", months: 1, expected: "rub", actual: "eur" },
      { kind: "price_mismatch", months: 1, localPrice: 250, tributePrice: 299 },
    ]);
  });

  it("ignores sub-cent rounding", () => {
    const result = applySubscriptionToPeriodRows(
      subscription,
      [{ months: 1, rub: 299.001 }],
      "RUB"
    );

    expect(result.issues).toEqual([]);
  });
});

describe("checkPeriodRows", () => {
  it("passes bindings that match the catalog", () => {
    const rows = [
      { months: 1, rub: 299, tribute_subscription_id: 101, tribute_period_id: 1001 },
      { months: 3, rub: 799, tribute_subscription_id: "101", tribute_period_id: "1003" },
    ];

    expect(checkPeriodRows(catalog, rows, "RUB")).toEqual([]);
  });

  it("skips rows without a full binding", () => {
    const rows = [{ months: 1, rub: 299, tribute_subscription_id: 101, tribute_period_id: "" }];

    expect(checkPeriodRows(catalog, rows, "RUB")).toEqual([]);
  });

  it("reports an unknown subscription and an unknown period", () => {
    const rows = [
      { months: 1, tribute_subscription_id: 999, tribute_period_id: 1001 },
      { months: 3, tribute_subscription_id: 101, tribute_period_id: 4242 },
    ];

    expect(checkPeriodRows(catalog, rows, "RUB")).toEqual([
      { kind: "unknown_subscription", months: 1, subscriptionId: 999 },
      { kind: "unknown_period", months: 3, periodId: 4242, subscriptionId: 101 },
    ]);
  });

  it("reports a period bound to the wrong duration", () => {
    const rows = [{ months: 3, tribute_subscription_id: 101, tribute_period_id: 1001 }];

    expect(checkPeriodRows(catalog, rows, "RUB")).toEqual([
      { kind: "period_months_mismatch", months: 3, periodId: 1001, actualPeriod: "monthly" },
    ]);
  });
});

describe("product bindings", () => {
  it("fills the product id and its checkout link", () => {
    const result = applyProductToTrafficRow(product, { gb: 50, price: 199 }, "RUB");

    expect(result.values).toEqual({
      tribute_product_id: 501,
      tribute_product_link: "https://web.tribute.tg/p/501",
    });
    expect(result.issues).toEqual([]);
  });

  it("reports a drifted product price", () => {
    const rows = [{ gb: 50, price: 149, tribute_product_id: 501 }];

    expect(checkTrafficRows(catalog, rows, "RUB")).toEqual([
      { kind: "product_price_mismatch", gb: 50, localPrice: 149, tributePrice: 199 },
    ]);
  });

  it("reports a product that no longer exists", () => {
    const rows = [{ gb: 10, price: 99, tribute_product_id: 777 }];

    expect(checkTrafficRows(catalog, rows, "RUB")).toEqual([
      { kind: "unknown_product", gb: 10, productId: 777 },
    ]);
  });
});

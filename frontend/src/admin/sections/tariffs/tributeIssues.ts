import type { TributeIssue } from "$lib/admin/tributeCatalog";
import type { TranslateFn } from "./tariffEditorTabUtils.js";

/** One admin-readable line per divergence between the tariff and Tribute. */
export function tributeIssueText(at: TranslateFn, issue: TributeIssue): string {
  switch (issue.kind) {
    case "missing_period":
      return at(
        "tariff_tribute_issue_missing_period",
        { months: issue.months },
        "{months} mo.: this subscription has no such period in Tribute"
      );
    case "unknown_subscription":
      return at(
        "tariff_tribute_issue_unknown_subscription",
        { months: issue.months, id: issue.subscriptionId },
        "{months} mo.: subscription #{id} was not found in Tribute"
      );
    case "unknown_period":
      return at(
        "tariff_tribute_issue_unknown_period",
        { months: issue.months, id: issue.periodId, subscription: issue.subscriptionId },
        "{months} mo.: period #{id} does not belong to subscription #{subscription}"
      );
    case "period_months_mismatch":
      return at(
        "tariff_tribute_issue_period_mismatch",
        { months: issue.months, id: issue.periodId, period: issue.actualPeriod },
        '{months} mo.: period #{id} is billed as "{period}" in Tribute'
      );
    case "price_mismatch":
      return at(
        "tariff_tribute_issue_price",
        { months: issue.months, tribute: issue.tributePrice, local: issue.localPrice },
        "{months} mo.: Tribute charges {tribute}, the local price is {local}"
      );
    case "currency_mismatch":
      return at(
        "tariff_tribute_issue_currency",
        { months: issue.months, actual: issue.actual.toUpperCase(), expected: issue.expected },
        "{months} mo.: the subscription is priced in {actual}, the tariff in {expected}"
      );
    case "unknown_product":
      return at(
        "tariff_tribute_issue_unknown_product",
        { gb: issue.gb, id: issue.productId },
        "{gb} GB: product #{id} was not found in Tribute"
      );
    case "product_price_mismatch":
      return at(
        "tariff_tribute_issue_product_price",
        { gb: issue.gb, tribute: issue.tributePrice, local: issue.localPrice },
        "{gb} GB: Tribute charges {tribute}, the local price is {local}"
      );
    case "product_currency_mismatch":
      return at(
        "tariff_tribute_issue_product_currency",
        { gb: issue.gb, actual: issue.actual.toUpperCase(), expected: issue.expected },
        "{gb} GB: the product is priced in {actual}, the tariff in {expected}"
      );
  }
}

/**
 * The one pair of escape helpers the rich-text modules share.
 *
 * It lives on its own so the serializer and the link detector can both use it
 * without importing each other: every string that reaches display HTML passes
 * through `escapeHtml`, and a second copy of that rule is the kind of thing
 * that drifts.
 */

export function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function unescapeHtml(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&");
}

/**
 * Turn the links people type into links they can tap.
 *
 * Support conversations are full of URLs that nobody wrapped in an anchor: a
 * customer pastes one from their client, and every message written before the
 * rich composer existed is plain text. Detection therefore happens at render
 * time rather than being baked into the stored body, so an old message becomes
 * clickable without being rewritten.
 *
 * Pure and DOM-free so it can be unit tested and reused by both surfaces.
 */

import { escapeHtml } from "./escape.js";

// Deliberately conservative: an explicit scheme, a bare `www.` host, or an
// e-mail address. Guessing that any `word.word` is a domain turns ordinary
// sentences ("см. файл config.py") into broken links.
const LINK_RE = /(?:https?:\/\/|www\.)[^\s<>"'`]+|[\w!#$%&'*+/=?^`{|}~.-]+@[\w-]+(?:\.[\w-]+)+/gi;

// Trailing punctuation almost always belongs to the sentence, not the URL.
const TRAILING_PUNCTUATION = /[.,;:!?»”’\]]+$/;

function trimTrailing(match: string): string {
  let candidate = match.replace(TRAILING_PUNCTUATION, "");
  // A closing bracket only belongs to the URL when the URL opened one, which
  // is what Wikipedia-style links rely on.
  while (candidate.endsWith(")") && countOf(candidate, ")") > countOf(candidate, "(")) {
    candidate = candidate.slice(0, -1);
  }
  return candidate;
}

function countOf(value: string, character: string): number {
  let total = 0;
  for (const char of value) if (char === character) total += 1;
  return total;
}

/** Absolute, scheme-qualified target for a detected candidate. */
export function linkHrefFor(candidate: string): string {
  if (/^https?:\/\//i.test(candidate)) return candidate;
  if (/^www\./i.test(candidate)) return `https://${candidate}`;
  return `mailto:${candidate}`;
}

/**
 * Escape `text` and wrap every detected link in an anchor.
 *
 * The input is raw text, never markup: callers hand over the text node they
 * are about to escape anyway, so nothing an author wrote can reach the output
 * unescaped.
 */
export function linkifyToHtml(text: string): string {
  const source = String(text ?? "");
  if (!source) return "";
  let result = "";
  let cursor = 0;
  LINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = LINK_RE.exec(source)) !== null) {
    const candidate = trimTrailing(match[0]);
    if (!candidate) continue;
    result += escapeHtml(source.slice(cursor, match.index));
    const href = escapeHtml(linkHrefFor(candidate)).replace(/"/g, "&quot;");
    result += `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow">${escapeHtml(
      candidate
    )}</a>`;
    cursor = match.index + candidate.length;
    LINK_RE.lastIndex = cursor;
  }
  return result + escapeHtml(source.slice(cursor));
}

/** Whether the text contains something {@link linkifyToHtml} would link. */
export function hasLink(text: string): boolean {
  LINK_RE.lastIndex = 0;
  return LINK_RE.test(String(text ?? ""));
}

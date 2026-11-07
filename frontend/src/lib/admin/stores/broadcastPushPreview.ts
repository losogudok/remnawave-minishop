/**
 * Plain-text Web Push preview for the admin broadcast composer.
 *
 * The backend strips Telegram HTML, turns the first line into the push title,
 * sends the remainder as the body, and uses only the first URL button as the
 * click target. Keep the live preview on exactly the same rules so an author
 * never sees a caption that the provider cannot deliver.
 */

export const PUSH_TITLE_MAX_LENGTH = 120;
export const PUSH_BODY_MAX_LENGTH = 200;
export const PUSH_URL_MAX_LENGTH = 512;

const BLOCK_BREAK_RE = /<br\s*\/?>|<\/(?:p|div|blockquote|pre|li|h[1-6])\s*>/gi;
const TAG_RE = /<[^>]*>/g;
const WHITESPACE_RE = /\s+/g;
const SENSITIVE_QUERY_PARTS = ["token", "access_token", "secret", "code", "session"];
const ALLOWED_PATH_PREFIXES = [
  "/trial",
  "/invite",
  "/partner",
  "/devices",
  "/support",
  "/settings",
  "/notifications",
  "/security",
  "/status",
  "/install",
  "/extensions",
  "/payments",
  "/billing",
];

export type PushPreviewButton = { kind: string; url: string };

type SettingsFieldLike = { key?: string; value?: unknown; default?: unknown };
type SettingsSectionLike = { fields?: SettingsFieldLike[] };

/**
 * Resolve the same WEBAPP_TITLE the backend uses as the push fallback title.
 * The admin settings snapshot is the source; the localized label is only a
 * development fallback when settings have not loaded yet.
 */
export function webappTitleFromSettingsSections(
  sections: SettingsSectionLike[] | null | undefined,
  fallback = "Minishop"
): string {
  for (const section of sections ?? []) {
    const field = section.fields?.find((item) => item.key === "WEBAPP_TITLE");
    if (!field) continue;
    return String(field.value ?? field.default ?? "").trim();
  }
  return fallback;
}

export type PushPreviewContent = {
  title: string;
  body: string;
  titleLength: number;
  bodyLength: number;
  titleTruncated: boolean;
  bodyTruncated: boolean;
};

function unescapeEntities(value: string): string {
  return value.replace(
    /&(#(?:x[0-9a-f]+|[0-9]+)|lt|gt|quot|apos|nbsp|amp);/gi,
    (entity: string, reference: string) => {
      if (!reference.startsWith("#")) {
        const named: Record<string, string> = {
          lt: "<",
          gt: ">",
          quot: '"',
          apos: "'",
          nbsp: " ",
          amp: "&",
        };
        return named[reference.toLowerCase()] ?? entity;
      }
      const digits = reference.slice(1);
      const codepoint =
        digits[0].toLowerCase() === "x" ? parseInt(digits.slice(1), 16) : Number(digits);
      return codepoint > 0 && codepoint <= 0x10ffff && !(codepoint >= 0xd800 && codepoint <= 0xdfff)
        ? String.fromCodePoint(codepoint)
        : entity;
    }
  );
}

/** Strip Telegram markup but keep the paragraph breaks the author wrote. */
export function plaintextForPushPreview(value: string): string {
  return unescapeEntities(
    String(value ?? "")
      .replace(BLOCK_BREAK_RE, "\n")
      .replace(TAG_RE, "")
  )
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .split("\n")
    .map((line) => line.replace(/[ \t]+$/g, ""))
    .join("\n")
    .replace(/\n+$/g, "");
}

function compact(value: string): string {
  return value.replace(WHITESPACE_RE, " ").trim();
}

function truncate(value: string, limit: number): string {
  const characters = [...value];
  if (characters.length <= limit) return value;
  return `${characters
    .slice(0, Math.max(0, limit - 1))
    .join("")
    .trimEnd()}…`;
}

/** Split rendered broadcast text into the payload the service worker receives. */
export function buildPushPreview(text: string, fallbackTitle: string): PushPreviewContent {
  const plain = plaintextForPushPreview(text);
  let rawTitle = fallbackTitle;
  let rawBody = "";
  if (plain.trim()) {
    const lines = plain.split("\n");
    const firstLine = lines[0].trim();
    if (!firstLine || [...firstLine].length > PUSH_TITLE_MAX_LENGTH) {
      rawBody = plain.trim();
    } else {
      rawTitle = firstLine;
      rawBody = lines.slice(1).join("\n").trim();
    }
  }
  const compactTitle = compact(rawTitle);
  const compactBody = compact(rawBody);
  const titleLength = [...compactTitle].length;
  const bodyLength = [...compactBody].length;
  return {
    title: truncate(compactTitle, PUSH_TITLE_MAX_LENGTH),
    body: truncate(compactBody, PUSH_BODY_MAX_LENGTH),
    titleLength,
    bodyLength,
    titleTruncated: titleLength > PUSH_TITLE_MAX_LENGTH,
    bodyTruncated: bodyLength > PUSH_BODY_MAX_LENGTH,
  };
}

function pathAllowed(path: string): boolean {
  if (path === "/") return true;
  return ALLOWED_PATH_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

function querySafe(query: string): boolean {
  for (const pair of query.split("&")) {
    if (!pair) continue;
    let key = pair.split("=")[0] ?? "";
    try {
      key = decodeURIComponent(key.replace(/\+/g, " "));
    } catch {
      return false;
    }
    const lowered = key.toLowerCase();
    if (SENSITIVE_QUERY_PARTS.some((part) => lowered.includes(part))) return false;
  }
  return true;
}

function safePath(raw: string): string {
  let parsed: URL;
  try {
    parsed = new URL(raw, "https://push.invalid");
  } catch {
    return "/";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "/";
  if (raw.startsWith("//") || raw.includes("\\") || raw.includes("\0")) return "/";
  const path = parsed.pathname || "/";
  if (!path.startsWith("/") || path.startsWith("//")) return "/";
  if (path === "/s" || path.startsWith("/s/")) return "/";
  if (!pathAllowed(path)) return "/";
  if (parsed.search && !querySafe(parsed.search.slice(1))) return "/";
  const target = `${path}${parsed.search}`;
  return target.length <= PUSH_URL_MAX_LENGTH ? target : "/";
}

/**
 * Resolve the single click target delivered to push: the first URL button when
 * it points at the same Web App origin, otherwise the app home path.
 */
export function pushPreviewClickTarget(buttons: PushPreviewButton[], origin = ""): string {
  const button = buttons.find((item) => item.kind === "url");
  if (!button) return "/";
  const raw = String(button.url || "").trim();
  if (!raw) return "/";
  if (raw.startsWith("/")) {
    if (raw.startsWith("//")) return "/";
    return safePath(raw);
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return "/";
  }
  if (!origin || parsed.origin.toLowerCase() !== origin.toLowerCase()) return "/";
  return safePath(`${parsed.pathname || "/"}${parsed.search}`);
}

export function pushPreviewHasUndeliveredButtons(buttons: PushPreviewButton[]): boolean {
  const urlButtons = buttons.filter((button) => button.kind === "url");
  return buttons.length > urlButtons.length || urlButtons.length > 1;
}

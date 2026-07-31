import { describe, expect, it } from "vitest";

import { hasLink, linkHrefFor, linkifyToHtml } from "./linkify";

describe("linkifyToHtml", () => {
  it("links an explicit URL and escapes the text around it", () => {
    expect(linkifyToHtml("see <https://x.dev/a>")).toBe(
      'see &lt;<a href="https://x.dev/a" target="_blank" rel="noopener noreferrer nofollow">https://x.dev/a</a>&gt;'
    );
  });

  it("gives a bare host and an address a scheme", () => {
    expect(linkHrefFor("www.x.dev")).toBe("https://www.x.dev");
    expect(linkHrefFor("help@x.dev")).toBe("mailto:help@x.dev");
  });

  it("leaves sentence punctuation out of the link", () => {
    expect(linkifyToHtml("open https://x.dev/a.")).toContain(">https://x.dev/a</a>.");
    expect(linkifyToHtml("(see https://x.dev/a)")).toContain(">https://x.dev/a</a>)");
  });

  it("keeps a closing bracket the link itself opened", () => {
    expect(linkifyToHtml("https://x.dev/a_(b)")).toContain(">https://x.dev/a_(b)</a>");
  });

  it("does not invent links out of ordinary words", () => {
    expect(linkifyToHtml("open config.py first")).toBe("open config.py first");
    expect(hasLink("open config.py first")).toBe(false);
  });

  it("cannot be used to inject markup through the link text", () => {
    expect(linkifyToHtml('https://x.dev/"><script>')).not.toContain("<script>");
  });
});

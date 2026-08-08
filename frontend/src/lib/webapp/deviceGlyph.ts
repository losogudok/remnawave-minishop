// Pick the icon for a connected device. The panel gives us free-form strings
// (`platform`, `os_version`, `deviceModel`, `userAgent`) rather than an enum, so
// this is deliberately a keyword match over everything we know about a device.
//
// Two independent axes: the hardware silhouette (what the device looks like) and
// the OS mark drawn on its screen. They are matched separately because a model
// name usually carries the form factor ("MacBook Air") while the platform field
// carries the OS ("macOS 15.4"), and either one can be missing.
//
// Matching is word-bounded: short tokens like "ios", "tv" and "pc" appear inside
// unrelated words ("studios", "network") often enough to matter.

export type DeviceShape = "phone" | "tablet" | "laptop" | "desktop" | "tv";
export type DeviceOs = "apple" | "android" | "windows" | "linux" | "unknown";

export interface DeviceGlyph {
  shape: DeviceShape;
  os: DeviceOs;
}

interface DeviceGlyphSource {
  display_name?: unknown;
  platform?: unknown;
  platform_label?: unknown;
  os_version?: unknown;
  user_agent?: unknown;
}

function haystack(device: DeviceGlyphSource | null | undefined): string {
  return [
    device?.display_name,
    device?.platform,
    device?.platform_label,
    device?.os_version,
    device?.user_agent,
  ]
    .map((part) => String(part ?? ""))
    .join(" ")
    .toLowerCase();
}

function matcher(words: readonly string[]): RegExp {
  return new RegExp(`\\b(?:${words.join("|")})\\b`);
}

const APPLE = matcher([
  "ios",
  "ipados",
  "iphone",
  "ipad",
  "macos",
  "mac os",
  "macintosh",
  "darwin",
]);
const ANDROID = matcher(["android", "harmonyos", "grapheneos"]);
const WINDOWS = matcher(["windows", "win32", "win64", "winnt", "microsoft"]);
const LINUX = matcher(["linux", "ubuntu", "debian", "fedora", "arch", "openwrt"]);
const MAC_COMPUTER = matcher(["macos", "mac os", "macintosh", "darwin"]);

// Android reports itself as Linux in user agents and iPadOS still says
// "Macintosh", so the specific platforms are tested before the generic ones.
function resolveOs(text: string): DeviceOs {
  if (ANDROID.test(text)) return "android";
  if (APPLE.test(text)) return "apple";
  if (WINDOWS.test(text)) return "windows";
  if (LINUX.test(text)) return "linux";
  return "unknown";
}

const TV = matcher(["tv", "tvos", "appletv", "shield", "firestick", "chromecast"]);
const TABLET = matcher(["ipad", "tablet", "tab", "pad", "surface"]);
const LAPTOP = matcher(["macbook", "laptop", "notebook", "thinkpad", "chromebook", "book"]);
const DESKTOP = matcher(["imac", "mac mini", "mac studio", "mac pro", "desktop", "pc"]);
const PHONE = matcher(["iphone", "phone", "pixel", "galaxy", "xiaomi", "redmi", "oneplus"]);

// The model name wins when it names a form factor; otherwise fall back to the
// shape each OS is most often used on.
const OS_DEFAULT_SHAPE: Record<DeviceOs, DeviceShape> = {
  apple: "phone",
  android: "phone",
  windows: "laptop",
  linux: "desktop",
  unknown: "phone",
};

function resolveShape(text: string, os: DeviceOs): DeviceShape {
  if (TV.test(text)) return "tv";
  if (TABLET.test(text)) return "tablet";
  if (LAPTOP.test(text)) return "laptop";
  if (DESKTOP.test(text)) return "desktop";
  if (PHONE.test(text)) return "phone";
  // Bare "macOS" with no model is a computer, not an iPhone.
  if (os === "apple" && MAC_COMPUTER.test(text)) return "laptop";
  return OS_DEFAULT_SHAPE[os];
}

export function resolveDeviceGlyph(device: DeviceGlyphSource | null | undefined): DeviceGlyph {
  const text = haystack(device);
  const os = resolveOs(text);
  return { shape: resolveShape(text, os), os };
}

// --- geometry -------------------------------------------------------------
// The hardware silhouette is stroked on a 24×24 grid (same as the lucide set
// used everywhere else); the OS mark is filled on its own 0..10 grid and scaled
// into the shell's screen box, so the two halves stay independent.

interface DeviceShell {
  outline: string[];
  screen: { x: number; y: number; size: number };
}

const DEVICE_SHELLS: Record<DeviceShape, DeviceShell> = {
  phone: {
    outline: [
      "M8.6 2h6.8A2.6 2.6 0 0 1 18 4.6v14.8A2.6 2.6 0 0 1 15.4 22H8.6A2.6 2.6 0 0 1 6 19.4V4.6A2.6 2.6 0 0 1 8.6 2z",
    ],
    screen: { x: 12, y: 12, size: 9 },
  },
  tablet: {
    outline: [
      "M5.6 2.5h12.8a2.1 2.1 0 0 1 2.1 2.1v14.8a2.1 2.1 0 0 1-2.1 2.1H5.6a2.1 2.1 0 0 1-2.1-2.1V4.6a2.1 2.1 0 0 1 2.1-2.1z",
    ],
    screen: { x: 12, y: 12, size: 10 },
  },
  laptop: {
    outline: [
      "M5.2 3.6h13.6a1.8 1.8 0 0 1 1.8 1.8v9.2H3.4V5.4a1.8 1.8 0 0 1 1.8-1.8z",
      "M2.6 16.2h18.8l1.35 2.35a1 1 0 0 1-.87 1.5H2.12a1 1 0 0 1-.87-1.5z",
    ],
    screen: { x: 12, y: 9.4, size: 9 },
  },
  desktop: {
    outline: [
      "M4.4 3.2h15.2a1.9 1.9 0 0 1 1.9 1.9v8.6a1.9 1.9 0 0 1-1.9 1.9H4.4a1.9 1.9 0 0 1-1.9-1.9V5.1a1.9 1.9 0 0 1 1.9-1.9z",
      "M12 15.6v4.4",
      "M8.6 20.4h6.8",
    ],
    screen: { x: 12, y: 9.4, size: 9.4 },
  },
  tv: {
    outline: [
      "M4 4.4h16a1.9 1.9 0 0 1 1.9 1.9v9.4a1.9 1.9 0 0 1-1.9 1.9H4a1.9 1.9 0 0 1-1.9-1.9V6.3A1.9 1.9 0 0 1 4 4.4z",
      "M8 21l1.9-3.4",
      "M16 21l-1.9-3.4",
    ],
    screen: { x: 12, y: 11, size: 9.4 },
  },
};

// `fill-rule: evenodd` is what punches the eyes out of the Android head and the
// belly/beak out of the penguin, so these stay single filled shapes.
const DEVICE_OS_MARKS: Record<DeviceOs, string[]> = {
  apple: [
    "M5.05 2.6C4.1 2.6 3.75 2.1 2.8 2.1 1.35 2.1.4 3.45.4 5.45c0 2.1 1.5 4.5 2.65 4.5.62 0 1.03-.45 2-.45s1.38.45 2 .45c1.15 0 2.65-2.4 2.65-4.5 0-2-.95-3.35-2.4-3.35-.95 0-1.3.5-2.25.5z",
    "M5.5 1.95c.95-.05 1.75-.9 1.75-1.95-1.05.05-1.9.9-1.75 1.95z",
  ],
  android: [
    "M1.1 6.5a3.9 3.9 0 0 1 7.8 0zM3.4 4.1a.5.5 0 1 1 0-1 .5.5 0 0 1 0 1zM6.6 4.1a.5.5 0 1 1 0-1 .5.5 0 0 1 0 1zM1.05 1.85l.6-.42 1.5 2.1-.6.42zM8.95 1.85l-.6-.42-1.5 2.1.6.42zM1.1 7.2h7.8v1.85a.95.95 0 0 1-.95.95H2.05a.95.95 0 0 1-.95-.95z",
  ],
  windows: ["M.7.9h3.9v3.9H.7zM5.4.9h3.9v3.9H5.4zM.7 5.6h3.9v3.9H.7zM5.4 5.6h3.9v3.9H5.4z"],
  linux: [
    "M5 .35c1.42 0 2.42 1.1 2.42 2.45 0 .5-.06.8.24 1.14.8.9 1.6 2.05 1.82 3.4.16 1.02-.14 1.8-.76 2.2-.5.33-1.05.2-1.34-.2-.4.5-1.32.76-2.38.76s-1.98-.26-2.38-.76c-.29.4-.84.53-1.34.2-.62-.4-.92-1.18-.76-2.2.22-1.35 1.02-2.5 1.82-3.4.3-.34.24-.64.24-1.14C2.58 1.45 3.58.35 5 .35zM4.3 1.85a.42.5 0 1 1 0 1 .42.5 0 0 1 0-1zM5.7 1.85a.42.5 0 1 1 0 1 .42.5 0 0 1 0-1zM5 3.05l.85.72-.85.6-.85-.6zM5 4.85c1.12 0 2 1.15 2 2.55S6.12 9.7 5 9.7 3 8.8 3 7.4s.88-2.55 2-2.55z",
  ],
  unknown: [],
};

export interface DeviceGlyphPaths extends DeviceGlyph {
  outline: string[];
  mark: string[];
  markTransform: string;
}

export function deviceGlyphPaths(device: DeviceGlyphSource | null | undefined): DeviceGlyphPaths {
  const glyph = resolveDeviceGlyph(device);
  const shell = DEVICE_SHELLS[glyph.shape];
  const scale = shell.screen.size / 10;
  const left = shell.screen.x - shell.screen.size / 2;
  const top = shell.screen.y - shell.screen.size / 2;
  return {
    ...glyph,
    outline: shell.outline,
    mark: DEVICE_OS_MARKS[glyph.os],
    markTransform: `translate(${left} ${top}) scale(${scale})`,
  };
}

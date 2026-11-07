#!/usr/bin/env node
import {
  cp,
  copyFile,
  lstat,
  mkdir,
  readdir,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const templatesDir = path.join(repoRoot, "backend", "bot", "app", "web", "templates");
const DEFAULT_OUT_DIR = path.join(repoRoot, "frontend-nginx-dist");
const outDir = resolveOutDir();

function resolveOutDir() {
  const argIndex = process.argv.indexOf("--out");
  if (argIndex === -1) {
    return DEFAULT_OUT_DIR;
  }
  const rawValue = process.argv[argIndex + 1];
  if (!rawValue) {
    throw new Error("--out requires a directory path");
  }
  return path.resolve(process.cwd(), rawValue);
}

async function pathExists(filePath) {
  try {
    await lstat(filePath);
    return true;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    throw error;
  }
}

async function copyIfExists(sourceName, targetName = sourceName) {
  const sourcePath = path.join(templatesDir, sourceName);
  if (!(await pathExists(sourcePath))) return false;
  await copyFile(sourcePath, path.join(outDir, targetName));
  return true;
}

async function copyDirectoryIfExists(sourceName, targetName = sourceName) {
  const sourcePath = path.join(templatesDir, sourceName);
  if (!(await pathExists(sourcePath))) return false;
  await cp(sourcePath, path.join(outDir, targetName), { recursive: true });
  return true;
}

async function linkOrCopy(targetName, aliasName) {
  const aliasPath = path.join(outDir, aliasName);
  try {
    await symlink(targetName, aliasPath);
  } catch (error) {
    if (!["EPERM", "EINVAL", "ENOSYS"].includes(error?.code)) {
      throw error;
    }
    await copyFile(path.join(outDir, targetName), aliasPath);
  }
}

function latestMatching(entries, pattern, fallbackName) {
  const matches = entries.filter((name) => pattern.test(name)).sort();
  return matches.at(-1) || fallbackName;
}

async function copyRuntimeAsset({ hashedName, stableName }) {
  const copied = await copyIfExists(hashedName);
  if (copied && hashedName !== stableName) {
    await linkOrCopy(hashedName, stableName);
  } else if (!copied) {
    await copyIfExists(stableName);
  }

  const gzipName = `${hashedName}.gz`;
  const gzipCopied = await copyIfExists(gzipName);
  if (gzipCopied && hashedName !== stableName) {
    await linkOrCopy(gzipName, `${stableName}.gz`);
  }

  const brotliName = `${hashedName}.br`;
  const brotliCopied = await copyIfExists(brotliName);
  if (brotliCopied && hashedName !== stableName) {
    await linkOrCopy(brotliName, `${stableName}.br`);
  }
}

function prepareIndexHtml(rawHtml, { cssName, jsName }) {
  // `modulepreload` rather than `preload as=script`: the bundle is an ES
  // module, and the browser has to fetch it with the module credentials mode
  // for the preload to be reused instead of duplicated.
  const jsPreload = `    <link rel="modulepreload" href="/${jsName}">`;
  const html = rawHtml
    .replace(/\r\n/g, "\n")
    .replace('href="/subscription_webapp.css"', `href="/${cssName}"`)
    .replace("</head>", `${jsPreload}\n  </head>`);
  // The static fallback shell is only served when the backend is unreachable,
  // so PWA wiring cannot be personalized per settings here. The manifest link
  // and best-effort worker registration are static; the backend-served shell
  // replaces both placeholders with the configured (nonce'd) variants, or
  // drops them entirely when PWA is disabled.
  const pwaHead = '    <link rel="manifest" href="/manifest.webmanifest" />';
  // Same coordinated update as the backend-served (nonce'd) registration in
  // backend/bot/app/web/webapp/pwa.py: waiting workers only activate on the
  // page's SKIP_WAITING signal, and the page reloads once on controllerchange.
  // The first activation only primes the controller flag (the static shell is
  // already current), so a later update still reloads.
  const pwaScript = [
    '    <script type="module">',
    '      if ("serviceWorker" in navigator) {',
    "        let pwaHadController = Boolean(navigator.serviceWorker.controller);",
    "        let pwaReactivated = false;",
    "        const pwaActivateWaiting = (registration) => {",
    "          const waiting = registration && registration.waiting;",
    "          if (waiting) {",
    '            try { waiting.postMessage({ type: "SKIP_WAITING" }); } catch (err) {}',
    "          }",
    "        };",
    '        navigator.serviceWorker.addEventListener("controllerchange", () => {',
    "          if (pwaHadController && !pwaReactivated) {",
    "            pwaReactivated = true;",
    "            window.location.reload();",
    "          }",
    "          pwaHadController = Boolean(navigator.serviceWorker.controller);",
    "        });",
    '        window.addEventListener("load", () => {',
    '          navigator.serviceWorker.register("/sw.js", { scope: "/" })',
    "            .then((registration) => {",
    "              pwaActivateWaiting(registration);",
    '              registration.addEventListener("updatefound", () => {',
    "                const installing = registration.installing;",
    "                if (installing) {",
    '                  installing.addEventListener("statechange", () => {',
    '                    if (installing.state === "installed" && navigator.serviceWorker.controller) {',
    "                      pwaActivateWaiting(registration);",
    "                    }",
    "                  });",
    "                }",
    "              });",
    "            })",
    "            .catch(() => {});",
    "        });",
    "      }",
    "    </script>",
  ].join("\n");
  const lines = html.split("\n");
  const output = lines
    .map((line) =>
      line.includes("WEBAPP_JS_SCRIPT")
        ? `    <script type="module" src="/${jsName}"></script>`
        : line
    )
    .map((line) => (line.includes("WEBAPP_PWA_HEAD") ? pwaHead : line))
    .map((line) => (line.includes("WEBAPP_PWA_SCRIPT") ? pwaScript : line))
    .filter(
      (line) =>
        !line.includes("WEBAPP_I18N_SCRIPT") &&
        !line.includes("WEBAPP_CONFIG_SCRIPT") &&
        !line.includes("WEBAPP_DEV_MOCK_START") &&
        !line.includes("WEBAPP_DEV_MOCK_END") &&
        !line.includes('src="/subscription_webapp.js"')
    )
    .join("\n");
  return output.endsWith("\n") ? output : `${output}\n`;
}

// The middle segment is matched loosely because a chunk name inherits every
// dot its entry module had (a `*.svelte.ts` store, for one). Missing a chunk
// here means the deployed app cannot load the screen that needs it.
const ADMIN_CHUNK_RE = /^subscription_webapp_admin\.[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+\.js$/;
const WEBAPP_CHUNK_RE = /^subscription_webapp\.[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+\.js$/;

function isAdminChunkName(name) {
  return (
    ADMIN_CHUNK_RE.test(name) &&
    name !== "subscription_webapp_admin.js" &&
    !name.startsWith("subscription_webapp_admin.min.")
  );
}

function isWebappChunkName(name) {
  return (
    WEBAPP_CHUNK_RE.test(name) &&
    !isAdminChunkName(name) &&
    !name.startsWith("subscription_webapp_admin.") &&
    !name.startsWith("subscription_webapp_docs_demo.") &&
    name !== "subscription_webapp.js" &&
    !name.startsWith("subscription_webapp.min.")
  );
}

async function copyChunkAssets(entries, matches) {
  const chunkNames = entries.filter(matches).sort();
  await Promise.all(
    chunkNames.map((chunkName) =>
      copyRuntimeAsset({ hashedName: chunkName, stableName: chunkName })
    )
  );
  return chunkNames;
}

async function main() {
  const entries = await readdir(templatesDir);
  const mainJsName = latestMatching(
    entries,
    /^subscription_webapp\.min\.[0-9a-f]{8}\.js$/,
    "subscription_webapp.js"
  );
  const mainCssName = latestMatching(
    entries,
    /^subscription_webapp\.[0-9a-f]{8}\.css$/,
    "subscription_webapp.css"
  );
  const adminJsName = latestMatching(
    entries,
    /^subscription_webapp_admin\.min\.[0-9a-f]{8}\.js$/,
    "subscription_webapp_admin.js"
  );
  const adminCssName = latestMatching(
    entries,
    /^subscription_webapp_admin\.[0-9a-f]{8}\.css$/,
    "subscription_webapp_admin.css"
  );

  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  await Promise.all([
    copyRuntimeAsset({ hashedName: mainJsName, stableName: "subscription_webapp.js" }),
    copyRuntimeAsset({ hashedName: mainCssName, stableName: "subscription_webapp.css" }),
    copyRuntimeAsset({ hashedName: adminJsName, stableName: "subscription_webapp_admin.js" }),
    copyRuntimeAsset({ hashedName: adminCssName, stableName: "subscription_webapp_admin.css" }),
  ]);
  const adminChunkNames = await copyChunkAssets(entries, isAdminChunkName);
  const webappChunkNames = await copyChunkAssets(entries, isWebappChunkName);
  const providerLogoAssetsCopied = await copyDirectoryIfExists("provider-logos");
  const fontAssetsCopied = await copyDirectoryIfExists("fonts");

  const indexTemplate = await readFile(path.join(templatesDir, "subscription_webapp.html"), "utf8");
  await writeFile(
    path.join(outDir, "index.html"),
    prepareIndexHtml(indexTemplate, { cssName: mainCssName, jsName: mainJsName }),
    "utf8"
  );

  console.log(
    `Prepared nginx assets in ${path.relative(repoRoot, outDir)}: ${mainJsName}, ${mainCssName}, ${adminJsName}, ${adminCssName}, ${webappChunkNames.length} app chunks, ${adminChunkNames.length} admin chunks, ${providerLogoAssetsCopied ? "provider logos" : "no provider logos"}, ${fontAssetsCopied ? "fonts" : "no fonts"}`
  );
}

await main();

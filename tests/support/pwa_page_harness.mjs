// Behavioral harness for the PWA *page* registration logic.
//
// The service-worker harness (pwa_sw_harness.mjs) only drives worker realms.
// This harness instead executes the page-side registration snippet (the inline
// <script> produced by backend/bot/app/web/webapp/pwa.py and duplicated for
// the static fallback in frontend/scripts/prepare_nginx_assets.mjs) against a
// fake navigator.serviceWorker / window, then replays the first-install ->
// update lifecycle and reports whether the page opts into the update and
// reloads when the new worker takes control.
//
// Input JSON from stdin:
// {
//   "script": "<inner page JS (without <script> tags)>",
//   "initialController": null | {"scriptURL": "..."}
// }
// Output JSON to stdout:
// {
//   "reloadsAfterFirstActivation": 0,
//   "skipWaitingMessages": 1,
//   "reloads": 1,
//   "hadControllerAtLoad": false,
// }
import vm from "node:vm";

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

async function flush(ticks = 4) {
  for (let i = 0; i < ticks; i += 1) {
    await Promise.resolve();
    await new Promise((resolve) => setImmediate(resolve));
  }
}

const input = JSON.parse(await readStdin());
const pageScript = input.script;
if (typeof pageScript !== "string" || !pageScript.includes("controllerchange")) {
  throw new Error("input.script must be the page registration JS");
}

const state = {
  reloads: 0,
  skipWaitingMessages: 0,
  controller: input.initialController ?? null,
};

const swListeners = { controllerchange: [] };
const loadListeners = [];

const registration = {
  waiting: null,
  installing: null,
  _updatefound: [],
  addEventListener(type, fn) {
    if (type === "updatefound") {
      this._updatefound.push(fn);
    }
  },
};

function makeWaitingWorker() {
  return {
    state: "installing",
    _statechange: [],
    addEventListener(type, fn) {
      if (type === "statechange") {
        this._statechange.push(fn);
      }
    },
    postMessage(message) {
      if (message && message.type === "SKIP_WAITING") {
        state.skipWaitingMessages += 1;
      }
    },
  };
}

const navigatorFake = {
  serviceWorker: {
    controller: state.controller,
    addEventListener(type, fn) {
      if (type === "controllerchange") {
        swListeners.controllerchange.push(fn);
      }
    },
    register: async () => registration,
  },
};

const windowFake = {
  addEventListener(type, fn) {
    if (type === "load") {
      loadListeners.push(fn);
    }
  },
  location: {
    reload() {
      state.reloads += 1;
    },
  },
};

const sandbox = {
  navigator: navigatorFake,
  window: windowFake,
  console: { log() {}, warn() {}, error() {} },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(pageScript, sandbox, { filename: "pwa-page.js" });

const hadControllerAtLoad = Boolean(navigatorFake.serviceWorker.controller);

// The page registers on window load.
for (const fn of [...loadListeners]) {
  await fn();
}
await flush();

// First-install tabs start uncontrolled: v1 installs, activates, and takes
// control. The page must NOT reload here (the shell is already current) but
// must prime its controller flag so a later update still reloads.
let reloadsAfterFirstActivation = state.reloads;
if (!hadControllerAtLoad) {
  navigatorFake.serviceWorker.controller = { scriptURL: "/sw.js?v1" };
  for (const fn of [...swListeners.controllerchange]) {
    await fn();
  }
  await flush();
  reloadsAfterFirstActivation = state.reloads;
}

// A second generation appears while the tab is open: the page observes the
// waiting worker, sends SKIP_WAITING, and reloads once v2 takes control.
const waiting = makeWaitingWorker();
registration.waiting = waiting;
registration.installing = waiting;
for (const fn of [...registration._updatefound]) {
  await fn();
}
waiting.state = "installed";
for (const fn of [...waiting._statechange]) {
  await fn();
}
await flush();

navigatorFake.serviceWorker.controller = { scriptURL: "/sw.js?v2" };
for (const fn of [...swListeners.controllerchange]) {
  await fn();
}
await flush();

process.stdout.write(
  JSON.stringify({
    reloadsAfterFirstActivation,
    skipWaitingMessages: state.skipWaitingMessages,
    reloads: state.reloads,
    hadControllerAtLoad,
  }),
);

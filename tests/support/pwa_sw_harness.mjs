// Behavioral harness for the PWA service workers.
// Reads a scenario (JSON) from stdin, evaluates worker sources with Node's
// `vm` module against a fake ServiceWorkerGlobalScope, drives install /
// message / activate / fetch / push / notificationclick events, and prints
// the observable outcome as JSON to stdout.
//
// The harness owns Cache Storage, network stubs, and event plumbing; worker
// realms share the cache store (like browsers do), so a `reload` step models
// a deploy: the new worker installs "waiting" while the previous worker and
// its cache generation stay intact until activation.
//
// Scenario schema:
// {
//   "origin": "https://app.example.com",
//   "sources": {"v1": "<worker js>", "v2": "<worker js>"},
//   "initialRealm": "v1",
//   "clients": [{"url": "https://app.example.com/"}],
//   "caches": {"<name>": {"<url>": {"status": 200, "body": "..."}}},
//   "network": {"<url>": {"status": 200, "body": "..."} | {"error": "boom"}},
//   "steps": [
//     {"run": "install", "realm": 0},
//     {"run": "message", "realm": 1, "data": {"type": "SKIP_WAITING"}},
//     {"run": "activate", "realm": 0},
//     {"run": "setFail", "open": true, "match": false},
//     {"run": "fetch", "realm": 0, "url": "/asset.js", "mode": "", "method": "GET"},
//     {"run": "push", "realm": 0, "data": {...} | null, "dataError": false},
//     {"run": "notificationclick", "realm": 0, "url": "/target"},
//     {"run": "reload", "source": "v2"}
//   ]
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

const scenario = JSON.parse(await readStdin());
const ORIGIN = scenario.origin || "https://app.example.com";

const state = {
  caches: new Map(),
  network: scenario.network || {},
  failOpen: false,
  failMatch: false,
  fetchCalls: [],
  deletedCaches: [],
};
for (const [name, entries] of Object.entries(scenario.caches || {})) {
  state.caches.set(name, new Map(Object.entries(entries)));
}

function makeResponse(url, entry) {
  const status = entry.status ?? 200;
  const response = {
    ok: status >= 200 && status < 300,
    status,
    type: entry.type ?? "basic",
    url,
    _body: entry.body ?? "",
  };
  response.clone = () => ({ ...response });
  return response;
}

async function stubFetch(input) {
  const url = typeof input === "string" ? input : input.url;
  const entry = state.network[url];
  if (!entry) {
    return makeResponse(url, { status: 404, body: "not found" });
  }
  if (entry.error) {
    throw new Error(entry.error);
  }
  return makeResponse(url, entry);
}

function cacheHandle(name) {
  if (!state.caches.has(name)) {
    state.caches.set(name, new Map());
  }
  const store = state.caches.get(name);
  return {
    match: async (request) => {
      if (state.failMatch) {
        throw new Error("cache match failed");
      }
      const url = typeof request === "string" ? request : request.url;
      const entry = store.get(url);
      return entry ? makeResponse(url, entry) : undefined;
    },
    put: async (request, response) => {
      const url = typeof request === "string" ? request : request.url;
      store.set(url, { status: response.status, body: response._body, type: response.type });
    },
  };
}

const stubCaches = {
  keys: async () => [...state.caches.keys()],
  open: async (name) => {
    if (state.failOpen) {
      throw new Error("cache open failed");
    }
    return cacheHandle(name);
  },
  delete: async (name) => {
    state.deletedCaches.push(name);
    return state.caches.delete(name);
  },
};

const realms = [];

function createRealm(source) {
  const index = realms.length;
  const listeners = {};
  const realm = {
    index,
    listeners: Object.keys(listeners),
    skipWaitingCalls: 0,
    claimCalls: 0,
    unregisterCalls: 0,
    closedNotifications: 0,
    notifications: [],
    openedWindows: [],
    focusedClients: [],
  };
  const clients = (scenario.clients || [{ url: `${ORIGIN}/` }]).map((client) => ({
    url: client.url,
    focus: async () => {
      realm.focusedClients.push(client.url);
      return client;
    },
  }));
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (type, fn) => {
      if (!listeners[type]) {
        listeners[type] = [];
      }
      listeners[type].push(fn);
    },
    skipWaiting: async () => {
      realm.skipWaitingCalls += 1;
    },
    registration: {
      showNotification: async (title, options) => {
        realm.notifications.push({ title, options });
      },
      unregister: async () => {
        realm.unregisterCalls += 1;
        return true;
      },
    },
    clients: {
      claim: async () => {
        realm.claimCalls += 1;
      },
      matchAll: async () => clients,
      openWindow: async (url) => {
        realm.openedWindows.push(url);
        return null;
      },
    },
  };
  const sandbox = {
    self,
    caches: stubCaches,
    fetch: (...args) => {
      const url = typeof args[0] === "string" ? args[0] : args[0].url;
      state.fetchCalls.push({ realm: index, url });
      return stubFetch(...args);
    },
    URL,
    console: { log() {}, warn() {}, error() {} },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: `sw-realm${index}.js` });
  realm.listeners = Object.keys(listeners);
  realms.push({ realm, listeners });
  return index;
}

function makeEvent() {
  const event = { _waited: [], _responded: null };
  event.waitUntil = (promise) => {
    event._waited.push(Promise.resolve(promise));
  };
  event.respondWith = (promise) => {
    event._responded = Promise.resolve(promise);
  };
  return event;
}

async function settle(event) {
  const pending = [...event._waited];
  if (event._responded) {
    pending.push(event._responded.catch(() => undefined));
  }
  await Promise.allSettled(pending);
  // Flush one extra macrotask so chained .then/.catch handlers land.
  await new Promise((resolve) => setImmediate(resolve));
  await Promise.allSettled(pending);
}

const stepResults = [];

async function runStep(step, stepIndex) {
  if (step.run === "setFail") {
    state.failOpen = Boolean(step.open);
    state.failMatch = Boolean(step.match);
    stepResults.push({ step: stepIndex, run: "setFail" });
    return;
  }
  if (step.run === "reload") {
    const source = scenario.sources[step.source];
    if (typeof source !== "string") {
      throw new Error(`unknown source: ${step.source}`);
    }
    const index = createRealm(source);
    stepResults.push({ step: stepIndex, run: "reload", realm: index });
    return;
  }
  const { realm: realmEntry } = realms[step.realm ?? 0] || {};
  if (!realmEntry) {
    throw new Error(`unknown realm: ${step.realm}`);
  }
  const { listeners } = realms[step.realm ?? 0];
  const handlers = listeners[step.run === "message" ? "message" : step.run] || [];

  if (step.run === "fetch") {
    const event = makeEvent();
    const absoluteUrl = new URL(step.url, ORIGIN).href;
    event.request = {
      method: step.method || "GET",
      url: absoluteUrl,
      mode: step.mode || "",
    };
    const fetchCallsBefore = state.fetchCalls.length;
    for (const handler of handlers) {
      await handler(event);
    }
    let outcome;
    if (!event._responded) {
      outcome = { responded: false };
    } else {
      try {
        const response = await event._responded;
        await settle(event);
        outcome = {
          responded: true,
          status: response ? response.status : null,
          body: response ? response._body : null,
          cacheHit: state.fetchCalls.length === fetchCallsBefore,
        };
      } catch (error) {
        outcome = { responded: true, rejected: String((error && error.message) || error) };
      }
    }
    stepResults.push({ step: stepIndex, run: "fetch", realm: step.realm ?? 0, ...outcome });
    return;
  }

  const event = makeEvent();
  if (step.run === "message") {
    event.data = step.data;
  } else if (step.run === "push") {
    event.data = step.dataError
      ? {
          json: () => {
            throw new Error("invalid json");
          },
        }
      : { json: () => step.data };
  } else if (step.run === "notificationclick") {
    event.notification = {
      data: { url: step.url },
      close: () => {
        realms[step.realm ?? 0].realm.closedNotifications += 1;
      },
    };
  }
  for (const handler of handlers) {
    await handler(event);
  }
  await settle(event);
  const entry = { step: stepIndex, run: step.run, realm: step.realm ?? 0 };
  if (step.run === "push" || step.run === "notificationclick") {
    entry.notifications = realms[step.realm ?? 0].realm.notifications.length;
    entry.openedWindows = [...realms[step.realm ?? 0].realm.openedWindows];
    entry.focusedClients = [...realms[step.realm ?? 0].realm.focusedClients];
  }
  stepResults.push(entry);
}

createRealm(scenario.sources[scenario.initialRealm || "v1"] ?? Object.values(scenario.sources)[0]);
for (let i = 0; i < (scenario.steps || []).length; i += 1) {
  await runStep(scenario.steps[i], i);
}

const output = {
  realms: realms.map(({ realm }) => realm),
  caches: Object.fromEntries(
    [...state.caches.entries()].map(([name, store]) => [name, [...store.keys()]]),
  ),
  deletedCaches: state.deletedCaches,
  fetchCalls: state.fetchCalls,
  stepResults,
};
process.stdout.write(JSON.stringify(output));

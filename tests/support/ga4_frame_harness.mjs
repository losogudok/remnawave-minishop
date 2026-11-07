import { pathToFileURL } from 'node:url';

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
}
const scenario = JSON.parse(input);
const frameOrigin = 'https://app.example.com';
const parentMessages = [];
const gtagCalls = [];
const appendedScripts = [];
const messageListeners = [];

globalThis.window = {
  location: { origin: frameOrigin, href: `${frameOrigin}/api/plugins/ga4funnel/frame` },
  addEventListener: (type, handler) => {
    if (type === 'message') messageListeners.push(handler);
  },
};
globalThis.window.parent = {
  postMessage: (message, targetOrigin) => {
    parentMessages.push({ message, targetOrigin });
  },
};
globalThis.location = globalThis.window.location;
if (scenario.gtagStub !== false) {
  globalThis.window.gtag = (...args) => {
    gtagCalls.push(args);
  };
}
globalThis.document = {
  head: {
    appendChild: (element) => {
      appendedScripts.push(element.src);
    },
  },
  documentElement: {
    appendChild: (element) => {
      appendedScripts.push(element.src);
    },
  },
  createElement: () => ({}),
};

const moduleUrl = pathToFileURL(scenario.modulePath).href;
await import(`${moduleUrl}?t=${Date.now()}`);

const dispatch = ({ origin, source, data }) => {
  for (const handler of [...messageListeners]) {
    handler({
      origin: origin === undefined ? frameOrigin : origin,
      source: source === 'other' ? {} : globalThis.window.parent,
      data,
    });
  }
};

for (const item of scenario.messages || []) {
  dispatch(item);
}

const dataLayer = Array.isArray(globalThis.window.dataLayer)
  ? globalThis.window.dataLayer.map((item) => (Array.isArray(item) ? item : Array.from(item)))
  : [];
console.log(
  JSON.stringify({
    appendedScripts,
    dataLayer,
    gtagCalls,
    gtagInstalled: typeof globalThis.window.gtag === 'function',
    parentMessages,
  })
);

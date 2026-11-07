import { pathToFileURL } from 'node:url';

let input = '';
for await (const chunk of process.stdin) {
  input += chunk;
}
const scenario = JSON.parse(input);
const appOrigin = 'https://app.example.com';
const frameMessages = [];
const windowMessageListeners = [];
const fetches = [];
const listeners = [];
let authenticated = scenario.authenticated !== false;
let frameElement = null;

function response(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return body;
    },
    async text() {
      return JSON.stringify(body);
    },
  };
}

globalThis.window = {
  location: {
    origin: appOrigin,
    href: scenario.locationHref || `${appOrigin}/`,
  },
  addEventListener: (type, handler) => {
    if (type === 'message') windowMessageListeners.push(handler);
  },
  removeEventListener: (type, handler) => {
    if (type !== 'message') return;
    const index = windowMessageListeners.indexOf(handler);
    if (index >= 0) windowMessageListeners.splice(index, 1);
  },
};
globalThis.document = {
  cookie: scenario.cookie || '',
  referrer: scenario.referrer || '',
  body: {
    appendChild: () => {},
  },
  createElement: (tag) => {
    if (tag !== 'iframe') return {};
    const contentWindow = scenario.failFrame
      ? null
      : {
          postMessage: (message, targetOrigin) => {
            frameMessages.push({ message, targetOrigin });
          },
        };
    const frame = {
      src: '',
      style: {},
      attributes: {},
      contentWindow,
      referrerPolicy: '',
      onload: null,
      removed: false,
      setAttribute: (name, value) => {
        frame.attributes[name] = value;
      },
      remove: () => {
        frame.removed = true;
      },
    };
    frameElement = frame;
    return frame;
  },
};

globalThis.fetch = async (url, options = {}) => {
  const target = String(url);
  const headers = options.headers || {};
  let body = null;
  if (options.body) {
    try {
      body = JSON.parse(options.body);
    } catch {
      body = options.body;
    }
  }
  fetches.push({
    url: target,
    method: options.method || 'GET',
    headers,
    body,
  });
  if (target.endsWith('/api/plugins/ga4funnel/config')) {
    return response(scenario.config || {});
  }
  if (target.endsWith('/api/plugins/ga4funnel/bind')) {
    return response({ ok: true });
  }
  return response({}, 404);
};

const moduleUrl = pathToFileURL(scenario.modulePath).href;
const adapterModule = await import(`${moduleUrl}?t=${Date.now()}`);
const adapter = adapterModule.mountOnboardingSignals({
  getAttribution: () => scenario.attribution || { code: '', utm: {} },
  isAuthenticated: () => authenticated,
  onSignal: (listener) => {
    listeners.push(listener);
    return () => {
      const index = listeners.indexOf(listener);
      if (index >= 0) listeners.splice(index, 1);
    };
  },
});

const deadline = Date.now() + 2000;
while ((listeners.length === 0 || !frameElement) && Date.now() < deadline) {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

const sendFrameMessage = (data) => {
  for (const handler of [...windowMessageListeners]) {
    handler({
      origin: appOrigin,
      source: frameElement ? frameElement.contentWindow : null,
      data,
    });
  }
};

if (frameElement && !scenario.failFrame) {
  sendFrameMessage({ type: 'ga4_ready' });
}

for (const signal of scenario.signals || []) {
  if (typeof signal.cookie === 'string') {
    globalThis.document.cookie = signal.cookie;
  }
  if (typeof signal.authenticated === 'boolean') {
    authenticated = signal.authenticated;
  }
  for (const listener of [...listeners]) {
    listener(signal);
  }
  await new Promise((resolve) => setTimeout(resolve, 30));
}

await new Promise((resolve) => setTimeout(resolve, 100));
const mounted = listeners.length > 0;
adapter.unmount();
console.log(
  JSON.stringify({
    appGtag: typeof globalThis.window.gtag,
    appDataLayer: globalThis.window.dataLayer ?? null,
    fetches,
    frame: frameElement
      ? {
          src: frameElement.src,
          sandbox: frameElement.attributes.sandbox || '',
          referrerPolicy:
            frameElement.referrerPolicy ||
            frameElement.attributes.referrerpolicy ||
            '',
          removed: frameElement.removed,
        }
      : null,
    frameMessages,
    mounted,
  })
);

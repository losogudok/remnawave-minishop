# GA4 through existing plugin contracts: feasibility

**Historical audit.** This note describes the 2026-10-31 baseline and an uncommitted
prototype, not the current implementation. The additive contracts and built-in GA4
plugin were subsequently implemented; see [analytics](docs/features/analytics.md)
and the [onboarding status](tasks/onboarding/README.md#implementation-status--2026-11-05).
Prototype paths rendered as code below are archival references to files absent
from the current checkout.

Source audit, 2026-10-31. Baseline: local `HEAD` = `e36085723a3b69a360f1fdb403eeff544de2bf23`.
Compared `git show HEAD:<path>` with the dirty checkout; selected remote upstream `main` files were
also checked for the guest/public boundaries below, rather than auditing all of current upstream.
Line links below refer to the working files unless explicitly marked HEAD. No runtime, browser, Panel,
GA property, deployment, or event-delivery proof was obtained. Only this note was written.

## Finding

**A custom backend plugin is feasible; the complete visit → signup → trial → first VPN connection
funnel on the existing guest and public screens is not covered by existing documented frontend
contracts alone.** Backend routes/storage/delivery can move into a plugin. Guest initialization,
safe SPA screen/action signals and browser-to-account attribution need an additive Core contract
or retained fork integration. No ready-made GA4 plugin was identified in the inspected source;
the capability list and built-ins provide extension infrastructure, not this funnel:
[capabilities](backend/bot/plugins/capabilities.py#L3),
[built-in telemetry](backend/bot/plugins/builtin/telemetry.py#L1).

## Guest text and installation images

The parent additionally read all four published pages: [plugins](https://minishop.minidoc.cc/development/plugins/),
[extensions](https://minishop.minidoc.cc/development/plugin-extensions/),
[UI composition](https://minishop.minidoc.cc/development/plugin-ui-composition/), and
[contract](https://minishop.minidoc.cc/development/plugin-contract/).
It fetched the following files from remote upstream `main` on 2026-10-31 to confirm the guest/public
limits independently of local HEAD. These moving-branch URLs identify the inspected source, not a pinned release.

- **Guest text below the service title:** upstream
  [AuthScreen](https://github.com/3252a8/remnawave-minishop/blob/main/frontend/src/webapp/auth/AuthScreen.svelte)
  renders the brand title without a plugin boundary or intro text key. Plugin locales can supply
  text, but cannot add a missing renderer there through the declared UI contracts.
- **Signed-in installation screen:** `user.install.content` can be replaced by a plugin, and
  `user.install.blocks` can append a view. The plugin can implement its own images/editor/storage.
  This does not add image support to the existing step renderer automatically; it is a custom view.
- **Panel step augmentation:** `GuideProvider` namespaces and adds platform fragments rather than
  modifying existing platforms/apps/steps. It is not the requested image overlay for an existing
  Panel instruction. [Merge implementation](backend/bot/plugins/extensions/guides.py#L67).
- **Public `/s/<token>` instruction:** upstream
  [AppModeContent](https://github.com/3252a8/remnawave-minishop/blob/main/frontend/src/webapp/AppModeContent.svelte)
  renders it outside the authenticated host, while upstream
  [guide_document](https://github.com/3252a8/remnawave-minishop/blob/main/backend/bot/app/web/webapp/guide_document.py)
  merges plugin guides only with a user ID. Existing personal guide contributions are expressly
  excluded from public shares in the SDK docs. Public extensions would need a separate contract
  that preserves the share-token access boundary.

For the exact requested screens, a prospective upstream change should expose guest/public UI,
guide media/augmentation, and sanitized analytics lifecycle signals. The editor, storage and GA4
integration can then be external. Alternatively, a plugin can serve separate public HTML pages
through `setup_web`; that changes the routes/journey and does not customize the existing screens.

## Supported backend surfaces

| Requirement | Actual public contract and limit |
| --- | --- |
| Collection/context endpoint, empty analytics frame, separate signed Panel webhook | `setup_web(ctx, app, *, scope)` registers routes after Core routes; scopes are `webapp` and `webhooks`. Use unique routes, preferably `/api/plugins/<owner>/…` for Web App proxying. This is not a template-injection or Core-handler replacement hook. [Plugin](backend/bot/plugins/spec.py#L299), [registration](backend/bot/app/web/web_server.py#L218), [webapp scope](backend/bot/app/web/web_server.py#L256), [nginx](deploy/docker/frontend/nginx.conf#L83). |
| Attribution/outbox tables | Namespaced append-only plugin `migrations()` and `ext_<plugin>_` tables; Core applies plugin chains. No need to alter existing Core migrations to build a new plugin. [contract](backend/bot/plugins/spec.py#L318), [runner](backend/db/migrator/__init__.py#L34), [rules](docs/development/plugins.md#L191). |
| Bounded Measurement Protocol delivery/retries | `worker_tasks()` returns `WorkerTaskSpec`; worker combines Core/plugin tasks. `queue_handlers()` adds unique provider names, so it cannot replace reserved `panel`. [hooks](backend/bot/plugins/spec.py#L306), [worker](backend/main_worker.py#L610), [reserved providers](backend/bot/plugins/loader.py#L254). |
| Domain subscriptions | Register `events.subscribe(name, handler)` in `setup`; handler gets `(event_name, flat_dict)`. Bus is process-local, awaits handlers and logs/skips failures; it is not a durable transactional outbox. Keep HTTP delivery out of these callbacks. [setup](backend/bot/plugins/spec.py#L272), [bus semantics](backend/bot/infra/events.py#L1), [dispatch](backend/bot/infra/events.py#L102). |

### Exact funnel event names and semantics

- **Signup:** Core publishes `user.registered`, not `user.created`. `create_user()` emits only
  on creation unless suppressed with `registered_via=None`; its emit occurs before the caller's
  surrounding commit. OAuth also has explicit post-transaction publication. A plugin gets IDs/source,
  not browser GA identifiers. [constants](backend/bot/infra/events.py#L38),
  [creation](backend/db/dal/user_dal.py#L164), [emit](backend/db/dal/user_dal.py#L226),
  [payload](backend/bot/infra/event_payloads.py#L184),
  [OAuth publication](backend/bot/app/web/webapp/external_oauth.py#L667).
- **Trial:** the specific event is `trial.activated` (`user_id`, `end_date`, `days`, `traffic_gb`),
  emitted by trial activation and the paid-trial fulfillment path. `subscription.created` lacks a
  trial discriminator and is not an equivalent trigger. Trial emission can be disabled; `commit`
  and `emit_event` are independent flags. [payloads](backend/bot/infra/event_payloads.py#L140),
  [trial emission](backend/bot/services/subscription_service_impl/trial.py#L227),
  [paid trial](backend/bot/payment_providers/shared/success.py#L763).
- **First connection:** subscribe to `panel.webhook_received` and filter
  `payload["event"] == "user.first_connected"`. HEAD already emits this generic notification
  before event-specific handling (`HEAD:panel_webhook_service.py:197–203`); the working equivalent
  is [here](backend/bot/services/panel_webhook_service.py#L198). This is an incoming **Panel** event,
  not a Core `user.first_connected` subscription. Its public payload has only `event`,
  `panel_user_uuid`, `telegram_id`: no internal user ID, first-connection timestamp or raw Panel
  data. Resolve the account locally and deduplicate. [payload](backend/bot/infra/event_payloads.py#L363).
  The verified webhook path checks HMAC before queueing
  ([verification](backend/bot/services/panel_webhook_service.py#L801)). `device.connected`
  includes device data/time but does not mean the user's first VPN connection
  ([separate contract](backend/bot/infra/event_payloads.py#L371)).
- **Reliability boundary:** `DurableSubscription` does not automatically bridge ordinary
  `events.emit` notifications. Durable delivery requires `extensions.jobs.publish` with a session;
  Core specifically wires transactional `payment.succeeded`, not signup/trial/Panel events.
  A plugin can persist/reconcile its own records, but cannot claim atomic capture in the original
  signup transaction from the current bus callback. For authoritative occurrence time, re-read
  confirmed Panel first-connection data or expose a richer additive event; alternatively accept
  a separately authenticated webhook on a plugin-owned route.
  [durable publish](backend/bot/plugins/extensions/jobs.py#L121),
  [Core payment wiring](backend/bot/plugins/extensions/registry.py#L120).

## Frontend lifecycle and CSP

- `UserExtensions` is inside `AuthenticatedScreens`; login renders `AuthScreen` and public `/s/<token>`
  renders `InstallGuideScreen` through separate branches. Neither branch mounts `UserExtensions`.
  Runtime descriptors **and assets** require a user session. An authenticated slot therefore cannot
  count the original guest visit or initialize on an anonymous public guide.
  [authenticated host](frontend/src/webapp/AuthenticatedScreens.svelte#L452),
  [public branch](frontend/src/webapp/AppModeContent.svelte#L410),
  [login branch](frontend/src/webapp/AppModeContent.svelte#L468),
  [runtime/assets auth](backend/bot/app/web/webapp/extension_runtime.py#L26).
- Descriptors load on screen/language changes, but the JS entry is imported only when a view mounts;
  `mountView`/optional `updateView`/`unmountView` describe that view's lifetime. `ExtensionHost`
  exposes requests, navigation, commerce, resource and UI helpers, with no global analytics,
  auth-transition or screen-observer subscription. A plugin may instrument its own mounted view.
  [descriptor loading](frontend/src/webapp/extensions/UserExtensions.svelte#L128),
  [import/lifecycle](frontend/src/webapp/extensions/UserPluginHost.svelte#L100),
  [host surface](frontend/src/lib/webapp/extensionHost.ts#L35).
- HEAD's backend HTML CSP allows self/Telegram scripts and self/Telegram OAuth connections, not
  Google (`HEAD:assets.py:158–183`). The dirty fork explicitly adds Google script/connect origins
  when GA is enabled [here](backend/bot/app/web/webapp/assets.py#L158). There is no declared plugin
  CSP contribution in [capabilities](backend/bot/plugins/capabilities.py#L3) or
  [extension contributions](backend/bot/plugins/extensions/contracts.py#L183). A plugin-owned HTML
  response can set its own CSP (Core uses `setdefault`), but serving the frame still does not mount
  it on guest screens. Server-to-server collection does not need browser Google CSP permissions.
- Trusted plugin JS is not sandboxed, so attaching global DOM/history/fetch observers is technically
  possible after mount. Likewise a Python plugin receives the aiohttp app and could inject response
  rewriting middleware. These are **not documented analytics/template contracts**; they depend on
  Core DOM, route/render details and deployment behavior. They do not supply a supported early
  guest lifecycle. Keep listeners scoped and cleaned up per the
  [published UI rules](docs/development/plugin-ui-composition.md#L182).

## What the dirty fork currently supplies beyond HEAD

HEAD contains none of `analytics.ts`, `ga4Frame.ts`, backend `ga4_*` services/DAL/models or migration
`0108_ga4_onboarding` (`git ls-tree -r HEAD` checked). Existing plugin hooks, event contracts,
user-creation/trial emitters and frontend plugin host/runtime have no textual delta in the inspected
files; many worktree changes are mode-only. The fork instead adds:

- Root-level guest/public/authenticated tracking and browser context binding in
  [App](frontend/src/App.svelte#L360); safe virtual `/analytics/<screen>` locations and an empty
  same-origin iframe in analytics (`frontend/src/lib/webapp/analytics.ts:120`, historical prototype),
  tag bootstrap (`frontend/src/lib/webapp/ga4Frame.ts:20`, historical prototype).
- Registration enrollment inside actual creation transactions in
  [email](backend/bot/app/web/webapp/auth_email.py#L264),
  [Telegram](backend/bot/app/web/webapp/auth_referral.py#L360),
  [OAuth](backend/bot/app/web/webapp/external_oauth.py#L619); authenticated binding in
  context route (`backend/bot/app/web/webapp/analytics.py:38`, historical prototype); durable
  storage migration (`backend/db/migrator/chain_0108_ga4_onboarding.py:10`, historical prototype).
- Raw Panel payload/timestamp handling beyond the public generic event in
  [webhook branch](backend/bot/services/panel_webhook_service.py#L206),
  timestamp extraction (`backend/bot/services/ga4_analytics.py:27`, historical prototype), and
  local binding (`backend/bot/services/ga4_panel_webhook.py:12`, historical prototype); server delivery only of
  `vpn_first_connected` in worker payload (`backend/bot/services/ga4_delivery_worker.py:42`, historical prototype).

**Static discrepancy:** the initiating `trial_started` call is in `applyPromo()` immediately after
`/api/promo/apply`, before the `requires_checkout` check; `activateTrial()` has no analytics call.
The other call in `analytics.ts` only flushes an already queued trial event after browser binding.
Consequently code application can falsely count as trial activation, while actual activation does
not initiate this event. The existing backend `trial.activated` event is the appropriate source.
[misplaced call](frontend/src/lib/webapp/stores/actionsStore.svelte.ts#L169),
[trial path](frontend/src/lib/webapp/stores/actionsStore.svelte.ts#L339).

## Practical alternatives

1. **Portable complete integration:** retain/add a small Core lifecycle contract that initializes
   opted-in analytics before auth on guest/public screens, supplies allowlisted screen/action
   signals and authenticated browser binding, and defines CSP behavior. Put GA settings/secrets,
   context storage, dedupe, reconciliation and delivery in the plugin. This is a proposed additive
   design, not an existing capability; the missing surfaces are evidenced above.
2. **Strictly existing contracts:** implement backend business milestones plus analytics of the
   plugin's own views or a separate plugin-owned landing page. Clearly label this reduced coverage;
   it cannot reconstruct anonymous visits/clicks on existing Core screens. Never send whole event
   payloads to GA: signup contains email/Telegram/profile identifiers, and guide URLs carry tokens
   ([signup payload](backend/bot/infra/event_payloads.py#L184),
   [public token handling](frontend/src/webapp/AppModeContent.svelte#L422)).
3. For a joined GA4 web funnel, bind the actual tag-generated `client_id`; an invented ID from an
   account/Telegram ID does not recover the original browser acquisition. Keep Measurement Protocol
   `api_secret` server-side. Google describes Measurement Protocol as augmenting tag collection;
   server-only reporting is partial. See official
   [Measurement Protocol architecture](https://developers.google.com/analytics/devguides/collection/protocol/ga4#full_server-to-server)
   and [web identifiers/secrets](https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events).

Validation scope: new-note whitespace and local Markdown link/file-line existence only. Full
pytest/Ruff/mypy/frontend/docs-site gates were not run for this standalone research note; the
pre-existing dirty product changes were not validated. No runtime proof is implied.

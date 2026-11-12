# Staging environment on remna

This document describes the independent Minishop staging environment used for
onboarding acceptance tests. Configuration and running services were verified on
**2026-11-05**. The deployed revision at that check was
`fdc559f5a18048ca1ff431ef18ee8c4893d75978`.

## Access

| Service | Address |
| --- | --- |
| Shop and admin UI | https://dev-app.lookmaimanengineer.cc/ |
| Webhook server | https://dev-wh.lookmaimanengineer.cc/ |
| Independent Remnawave Panel | https://dev-pnl.lookmaimanengineer.cc/ |
| Subscription Page host | https://dev-sub.lookmaimanengineer.cc/ |
| Protected test mailbox | https://dev-wh.lookmaimanengineer.cc/mail/ |

The webhook host serves API endpoints rather than a landing page. Subscription
pages require a personal subscription link; the subscription host's bare `/`
returns `404`.

All four DNS A records point to `89.106.89.53`. The existing Caddy edge provides
HTTPS and routes requests to staging-specific upstreams.

Connect from the development machine using the `remna` SSH alias and GitHub key:

```bash
ssh -i ~/.ssh/github -o IdentitiesOnly=yes remna
```

The SSH destination is `root@89.106.89.53:22`. GitHub Actions uses a separate
deployment key; the personal GitHub SSH key is used for operator access.

## Where staging runs

The deployment root and active Git checkout are both **`/opt/remnawave-dev`**,
on branch **`dev`** of
`https://github.com/losogudok/remnawave-minishop.git`.

| Path on remna | Purpose |
| --- | --- |
| `/opt/remnawave-dev/` | Source checkout and private staging runtime files |
| `/opt/remnawave-dev/.env` | Selects the staging Compose project for plain `docker compose` commands |
| `/opt/remnawave-dev/compose.local.yml` | Includes the staging definition with `stage.env` and `images.env` |
| `/opt/remnawave-dev/deploy/staging/compose.yml` | Active Compose definition |
| `/opt/remnawave-dev/deploy/staging/Caddyfile` | Source definition of staging edge routes |
| `/opt/remnawave-dev/scripts/deploy-staging.sh` | Deployment script invoked by Actions |
| `/opt/remnawave-dev/stage.env` | Private runtime settings and independent secrets |
| `/opt/remnawave-dev/images.env` | Three deployed application image digest references |
| `/opt/remnawave-dev/deployed-commit` | Last successfully deployed Git SHA |
| `/opt/remnawave-dev/mailpit-access.json` | Test mailbox URL, username, and password |
| `/opt/remnawave-dev/mailpit.htpasswd` | Hashed mailbox credentials used by Mailpit |
| `/opt/remnawave-dev/panel-admin.json` | Independent Panel administrator credentials |
| `/opt/remnawave-dev/staging-acceptance.json` | Recorded deployment and smoke-test evidence |
| `/opt/remnawave-dev/staging-backups/` | Private pre-migration shop database dumps |

Private runtime files and backups are excluded by `.gitignore` and `.dockerignore`;
`.env` and `compose.local.yml` are also ignored. The local Compose wrapper reads `stage.env` and `images.env`
on every invocation, so image references follow the last successful deployment.
Previously copied production configuration and scripts are preserved under
`backups/layout-before-root-*/` and are not used by staging. The tracked root
`docker-compose.yml` remains the standard production definition; the local `.env`
selects staging instead.

The active staging routes were added to `/opt/remnawave/caddy/Caddyfile`, which
is mounted by the existing `caddy-remnawave` container. Production routes were
preserved. A source Caddyfile in the staging checkout does not update that live
edge configuration automatically.

## Services and persistent data

The Docker Compose project name is **`minishop-staging`**. All staging service
names begin with `stage-`:

| Services | Responsibility |
| --- | --- |
| `stage-backend`, `stage-worker`, `stage-frontend` | Minishop API, background jobs, and web UI |
| `stage-migrate` | One-off shop schema and built-in plugin migrations |
| `stage-postgres`, `stage-redis` | Independent shop database and Redis |
| `stage-panel` | Remnawave Panel `3.4.7` |
| `stage-panel-db`, `stage-panel-redis` | Independent Panel PostgreSQL and Valkey |
| `stage-subscription` | Remnawave Subscription Page `7.2.6` |
| `stage-mailpit` | Mailpit `v1.33.0`, capturing test email |

Both PostgreSQL services use `postgres:17.11`. Shop Redis uses `redis:7-alpine`;
Panel Valkey uses `valkey/valkey:9-alpine`.

Persistent volumes are scoped to `minishop-staging`: `shop-db`, `shop-redis`,
`shop-data`, `panel-db`, `panel-valkey`, and `mailpit-data`. Their Docker names
start with `minishop-staging_`; production databases and application storage are
not reused. Database and Redis ports are not published.

Direct HTTP ports bind to loopback only:

| Service | Host binding |
| --- | --- |
| Shop frontend | `127.0.0.1:18082` |
| Webhook backend | `127.0.0.1:18080` |
| Panel | `127.0.0.1:13000` |
| Subscription Page | `127.0.0.1:13010` |

HTTP services join the existing `remnawave-network` for Caddy access, using unique
staging service names. Application services also use the staging project network.
The shop talks to `http://stage-panel:3000/api` in `PANEL_WRITE_MODE=live`.
Panel webhooks target `http://stage-backend:8080/webhook/panel`.

## Owner login and test email

The shop owner account is **`losogudok@gmail.com`**. Enter that email on the shop
login screen and obtain the verification code from the protected test mailbox.
Owner sign-in and access to admin settings were verified in that snapshot. The first owner role is assigned through `BOOTSTRAP_OWNER_EMAIL`; staging does not use the local QA seed or its known passwords.

Mailpit accepts authenticated SMTP from staging services on port `1025` and
stores messages locally. It has no external relay or forwarding configuration.
Login codes and notification messages are captured there rather than delivered
to external inboxes. Its web UI and API require HTTP Basic authentication; its
SMTP port is not published to the host.

Retrieve mailbox and Panel credentials through SSH:

```bash
ssh -i ~/.ssh/github -o IdentitiesOnly=yes remna \
  'cat /opt/remnawave-dev/mailpit-access.json /opt/remnawave-dev/panel-admin.json'
```

Credential files and `stage.env` have mode `0600`. This document intentionally
contains their locations without their secret values.

## Enabled features and remaining setup

| Feature | Verified configuration |
| --- | --- |
| Runtime profile | `APP_RUNTIME_MODE=staging` |
| Public QA authentication/payment shortcuts | Both disabled |
| Login introduction and guide screenshots | Included in the deployed onboarding implementation |
| `guide_media` plugin | Bundled with Core and loaded automatically |
| PWA | Enabled; manifest and service worker return `200` |
| Web Push | Enabled with an independent VAPID key pair; authenticated status reports configured |
| Email | Enabled through the protected Mailpit capture service |
| Telegram | Disabled; requires a separate test bot and its configuration |
| GA4 | Disabled; requires a test property, Measurement ID, and API secret |
| VPN node | Not connected; requires a separate registered test node |

The `guide_media`, `web_push`, and `ga4funnel` plugins ship with Core. They require
no separate installation. The worker is running for background delivery jobs.
See [Web Push](../features/web-push.md) and [GA4](../features/analytics.md)
for feature configuration. Staging uses independent credentials and VAPID keys; production bot and OAuth settings are not copied.

Configured Web Push does not establish device delivery. OS notifications, PWA
installation on physical devices, Telegram Mini App behavior, live GA4 reports,
and a real VPN connection remain separate acceptance checks.

## Automatic CI/CD

Every push to `dev` starts `.github/workflows/docker-dev.yml` (**Dev CI/CD**):

1. Run the reusable PR checks, Full-stack QA matrix, and Security workflows for
   the same commit SHA.
2. Build and publish backend, worker, and frontend images to GHCR, scan the
   published candidates with Trivy, and record their immutable digest references.
3. Download those references from the same workflow run and verify their source SHA.
4. Connect to remna through SSH with pinned host keys.
5. Check the source checkout, fetch `dev`, and reject a superseded commit before
   stopping application services.
6. Pull the scanned images and verify their OCI revision labels. The server does
   not rebuild them or select an application image through a mutable `dev` tag.
7. Stop staging backend/worker, save a shop database dump, run migrations, and
   start the app with health checks.
8. Write `images.env` and `deployed-commit`, then verify the deployed SHA and public
   manifest before reporting deployment success.

The GitHub environment is **`staging`**, restricted to branch `dev`, with no manual
approval gate. It contains variables `DEPLOY_HOST`, `DEPLOY_PORT`, `DEPLOY_USER`,
and `STAGING_APP_URL`, plus secrets `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`.

Actions temporarily uses its own short-lived `GITHUB_TOKEN` to pull private GHCR
images through `/opt/remnawave-dev/.docker/config.json`. That file is removed
after the deployment attempt. Workflow concurrency and a host-side lock prevent
parallel deployments; a running deployment is not cancelled by a newer push. The SSH-streamed deployment script is fully parsed before execution, and child processes have closed stdin so Compose cannot consume the remaining script.

Latest verified successful automatic deployment:
[run 42056540799](https://github.com/losogudok/remnawave-minishop/actions/runs/42056540799).

Manually start the full pipeline from an authenticated GitHub CLI:

```bash
gh workflow run docker-dev.yml --repo losogudok/remnawave-minishop --ref dev
gh run list --repo losogudok/remnawave-minishop \
  --branch dev --workflow docker-dev.yml --limit 5
```

## Operating commands

After connecting to remna, ordinary Compose commands select staging automatically:

```bash
cd /opt/remnawave-dev
docker compose ps -a
docker compose logs
docker compose logs --tail=100 stage-backend stage-worker stage-frontend
cat deployed-commit
docker compose config --quiet
```

The private `.env` contains only the operator defaults:

```dotenv
COMPOSE_PROJECT_NAME=minishop-staging
COMPOSE_FILE=/opt/remnawave-dev/compose.local.yml
STAGING_ENV_FILE=/opt/remnawave-dev/stage.env
```

The ignored `compose.local.yml` includes the tracked staging definition and reads
the current runtime settings and image references without duplicating secrets:

```yaml
include:
  - path: /opt/remnawave-dev/deploy/staging/compose.yml
    project_directory: /opt/remnawave-dev
    env_file:
      - /opt/remnawave-dev/stage.env
      - /opt/remnawave-dev/images.env
```

CI uses explicit Compose arguments and accepts both a checkout at the deployment
root and the previous nested `source/` layout. Commit and publish the repository
changes before the next automatic deployment: its clean-worktree guard rejects
uncommitted edits on the host.

After changing `stage.env`, recreate the affected services to load new values.
For application and SMTP setting changes:

```bash
docker compose up -d --no-build --pull never --force-recreate \
  --wait --wait-timeout 180 \
  stage-mailpit stage-backend stage-worker stage-frontend
```

A plain `restart` does not reload environment-file values. Preserve the stable
session/email secrets and VAPID keys across deployments.

## Deployment failures and recovery

Check the failing Actions step and staging service logs first. A failed migration
blocks application startup; the deployment script does not roll back the schema.
`staging-backups/<timestamp>-<commit-prefix>/shop.dump` contains the pre-migration
shop database snapshot. These dumps do not constitute a full backup of Panel,
uploaded files, credentials, or the entire staging environment.

Fix the failure and rerun the full pipeline for the current `dev` revision.
Returning to an earlier image after a schema change may require an explicit
database restore. Restore operations and volume removal are separate manual
maintenance actions. Avoid running the copied production Compose file or any
unscoped Docker cleanup against this host.

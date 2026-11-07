#!/usr/bin/env bash
# Invoked over SSH only after the dev commit and all three images pass CI.
set -Eeuo pipefail
umask 077

# Parse the complete streamed script before running commands. Compose may read
# stdin even for detached operations; it must never consume the remaining script.
main() {
  commit=${1:?Usage: deploy-staging.sh COMMIT BACKEND_IMAGE WORKER_IMAGE FRONTEND_IMAGE}
  [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA' >&2; exit 1; }
  backend_image=${2:?Missing backend digest}
  worker_image=${3:?Missing worker digest}
  frontend_image=${4:?Missing frontend digest}
  for target in backend worker frontend; do
    variable="${target}_image"
    image=${!variable}
    [[ "$image" =~ ^ghcr\.io/losogudok/remnawave-minishop-${target}@sha256:[0-9a-f]{64}$ ]] || {
      echo "Invalid immutable $target image reference" >&2; exit 1;
    }
  done

  stage_dir=${STAGING_DIR:-/opt/remnawave-dev}
  cd "$stage_dir"
  export DOCKER_CONFIG="$stage_dir/.docker"
  exec 9>.staging-deploy.lock
  flock -n 9 || { echo 'Another staging deployment is running' >&2; exit 1; }
  export STAGING_ENV_FILE="$stage_dir/stage.env"
  grep -qx 'APP_RUNTIME_MODE=staging' "$STAGING_ENV_FILE"
  grep -qx 'QA_AUTH_ENABLED=False' "$STAGING_ENV_FILE"
  grep -qx 'QA_PAYMENT_ENABLED=False' "$STAGING_ENV_FILE"

  cd source
  [[ $(git remote get-url origin) == https://github.com/losogudok/remnawave-minishop.git ]] || {
    echo 'Unexpected staging origin' >&2; exit 1;
  }
  [[ $(git branch --show-current) == dev ]] || { echo 'Staging must be on dev' >&2; exit 1; }
  [[ -z $(git status --porcelain --untracked-files=normal) ]] || {
    echo 'Staging source has local changes; refusing to overwrite them' >&2; exit 1;
  }
  git fetch origin dev --tags
  if [[ $(git rev-parse origin/dev) != "$commit" ]]; then
    echo 'Skipping superseded commit; the newer dev revision must pass CI first'
    exit 0
  fi
  git merge --ff-only "$commit"
  [[ $(git rev-parse HEAD) == "$commit" ]] || { echo 'Source differs from tested commit' >&2; exit 1; }

  candidate=$(mktemp "$stage_dir/.images.XXXXXX")
  trap 'rm -f "$candidate"' EXIT
  printf 'STAGING_BACKEND_IMAGE=%s\nSTAGING_WORKER_IMAGE=%s\nSTAGING_FRONTEND_IMAGE=%s\n' \
    "$backend_image" "$worker_image" "$frontend_image" > "$candidate"
  compose=(docker compose --project-name minishop-staging --project-directory "$stage_dir"
    --env-file "$STAGING_ENV_FILE" --env-file "$candidate" -f "$stage_dir/source/deploy/staging/compose.yml")
  "${compose[@]}" config --quiet
  # Download everything before interrupting the running application. The host
  # never builds or resolves a mutable dev/latest tag for application images.
  "${compose[@]}" pull
  for target in backend worker frontend; do
    variable="${target}_image"
    revision=$(docker image inspect "${!variable}" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
    [[ "$revision" == "$commit" ]] || { echo "$target revision mismatch" >&2; exit 1; }
  done

  "${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 180 stage-postgres stage-redis stage-mailpit stage-panel-db stage-panel-redis stage-panel stage-subscription
  "${compose[@]}" stop stage-backend stage-worker
  backup="$stage_dir/staging-backups/$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
  mkdir -p "$backup"
  "${compose[@]}" exec -T stage-postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup/shop.dump"
  test -s "$backup/shop.dump"
  "${compose[@]}" run --rm -T --interactive=false --no-deps --pull never stage-migrate
  "${compose[@]}" up -d --no-build --pull never --wait --wait-timeout 180 stage-backend stage-worker stage-frontend
  "${compose[@]}" ps -a
  mv "$candidate" "$stage_dir/images.env"
  printf '%s\n' "$commit" > "$stage_dir/deployed-commit"
  echo "Staging deployed: $commit"
}

main "$@" </dev/null

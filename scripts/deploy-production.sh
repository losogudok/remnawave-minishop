#!/usr/bin/env bash
# Run as the checkout owner on the production host, after CI succeeds.
set -Eeuo pipefail
umask 077

commit=${1:?Usage: deploy-production.sh TESTED_COMMIT}
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA' >&2; exit 1; }
cd "${DEPLOY_DIR:-/opt/remnawave-minishop}"
exec 9>.git/production-deploy.lock
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }

[[ $(git remote get-url origin) == https://github.com/losogudok/remnawave-minishop.git ]] || {
  echo 'Unexpected production origin' >&2; exit 1;
}
[[ $(git branch --show-current) == main ]] || { echo 'Production must be on main' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=normal) ]] || {
  echo 'Production checkout has local changes; refusing to overwrite them' >&2; exit 1;
}
git fetch origin main --tags
if [[ $(git rev-parse origin/main) != "$commit" ]]; then
  echo 'Skipping superseded commit; a newer main revision must pass CI first'
  exit 0
fi
previous_commit=$(git rev-parse HEAD)
git merge --ff-only "$commit"
[[ $(git rev-parse HEAD) == "$commit" ]] || { echo 'Checkout does not match tested commit' >&2; exit 1; }

# Build everything before interrupting the running application. No registry pull
# may replace these local images during migration or startup.
docker compose config --quiet
available_kb=$(df -Pk . | awk 'NR == 2 {print $4}')
(( available_kb >= 10 * 1024 * 1024 )) || {
  echo 'At least 10 GiB free disk space is required before building' >&2; exit 1;
}
docker compose build

backup="backups/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-${commit:0:12}"
mkdir -p "$backup"
cp .env "$backup/env"
printf '%s\n' "$previous_commit" > "$backup/previous-commit"
printf '%s\n' "$commit" > "$backup/target-commit"
# Quiesce application writers before taking the migration backup.
docker compose stop backend worker
docker compose exec -T postgres sh -c 'pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "$backup/database.dump"
test -s "$backup/database.dump"
docker compose run --rm --no-build --pull never migrate
docker compose up -d --no-build --pull never --wait --wait-timeout 180
docker compose ps -a
echo "Deployed $commit; pre-migration database backup: $backup/database.dump"

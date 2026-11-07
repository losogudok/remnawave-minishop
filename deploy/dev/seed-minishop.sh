#!/bin/sh
# Resolve fixture identities after the Panel seed; never guess sequence IDs.
set -eu

seed_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
panel_seed_users=$(
    PGHOST=remnawave-db PGPORT=5432 PGUSER=postgres PGPASSWORD=postgres PGDATABASE=postgres \
        psql -X -v ON_ERROR_STOP=1 -At -f "$seed_dir/seed-remnawave-identities.sql"
)

exec psql -X -v ON_ERROR_STOP=1 --single-transaction \
    --set "panel_seed_users=$panel_seed_users" -f "$seed_dir/seed-minishop.sql"

#!/usr/bin/env bash
#
# run_phase1.sh -- Run the Phase 1 acceptance checks inside the Compose db service.
#
# This is the recommended path on a host without psql: Docker Compose loads
# `.env` and passes the admin and role passwords to the db container, so the
# checks can connect over TCP without sourcing `.env` in the host shell.
#
# Usage:
#   db/tests/run_phase1.sh
#
# Optional:
#   SERVICE             Compose service name (default: db)
#   ALLOW_NON_TEST_DB   defaults to 1 here; the Compose database is normally
#                       `transducer`, not a `_test` database.

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
SERVICE=${SERVICE:-db}

if ! command -v docker >/dev/null 2>&1; then
    printf 'docker not found\n' >&2
    exit 2
fi

cd "$REPO_ROOT"

if ! docker compose ps --services --filter status=running 2>/dev/null | grep -qx "$SERVICE"; then
    printf "Compose service '%s' is not running; start it with: docker compose up -d %s\n" \
        "$SERVICE" "$SERVICE" >&2
    exit 2
fi

exec docker compose exec -T \
    -e ALLOW_NON_TEST_DB="${ALLOW_NON_TEST_DB:-1}" \
    "$SERVICE" bash -s < "$SCRIPT_DIR/verify_phase1.sh"

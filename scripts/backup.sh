#!/usr/bin/env bash
# Back up PostgreSQL and the dashboard state volume.
#
#   scripts/backup.sh
#
# Writes a custom-format pg_dump, a tar archive of the dashboard state volume,
# and a small manifest to BACKUP_DIR. The dump is verified with pg_restore
# before the temporary directory is promoted. Retention and optional rsync to a
# network share are controlled by environment variables; see docs/operations.md.
set -euo pipefail

# shellcheck source=scripts/_common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_repo

POSTGRES_DB=$(setting POSTGRES_DB transducer)
POSTGRES_USER=$(setting POSTGRES_USER transducer_admin)
POSTGRES_PASSWORD=$(require_setting POSTGRES_PASSWORD)
BACKUP_DIR=$(setting BACKUP_DIR /var/backups/transducer)
BACKUP_RETENTION_DAYS=$(setting BACKUP_RETENTION_DAYS 30)
BACKUP_MONTHLY_RETENTION=$(setting BACKUP_MONTHLY_RETENTION 12)
BACKUP_REMOTE=$(setting BACKUP_REMOTE "")

command -v flock >/dev/null 2>&1 || die "flock is required (install util-linux)"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR" 2>/dev/null || true

exec 9>"$BACKUP_DIR/.backup.lock"
flock -n 9 || die "another backup is already running"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
month=$(date -u +%Y-%m)
work=$(mktemp -d "$BACKUP_DIR/.tmp.XXXXXX")
trap 'rm -rf "$work"' EXIT

dump_name="${POSTGRES_DB}-${stamp}.dump"
dump_path="$work/$dump_name"

log "dumping database $POSTGRES_DB"
compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
    pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --compress=9 >"$dump_path"

log "verifying dump"
compose exec -T db pg_restore --list <"$dump_path" >/dev/null \
    || die "pg_restore could not read the dump"

state_name=""
state_path=""
if compose ps --status running --services 2>/dev/null | grep -qx dashboard; then
    log "archiving dashboard state"
    state_name="dashboard-state-${stamp}.tar.gz"
    state_path="$work/$state_name"
    compose exec -T dashboard \
        tar -czf - -C /var/lib/transducer-dashboard . >"$state_path"
    tar -tzf "$state_path" >/dev/null || die "dashboard state archive is unreadable"
else
    warn "dashboard is not running; skipping dashboard state"
fi

cat >"$work/backup-${stamp}.json" <<EOF
{
  "created_at": "$(timestamp)",
  "database": "$POSTGRES_DB",
  "dump": "$dump_name",
  "dashboard_state": "$state_name",
  "git_commit": "$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
}
EOF

mv "$dump_path" "$BACKUP_DIR/$dump_name"
if [[ -n "$state_path" ]]; then
    mv "$state_path" "$BACKUP_DIR/$state_name"
fi
mv "$work/backup-${stamp}.json" "$BACKUP_DIR/backup-${stamp}.json"

ln -sfn "$dump_name" "$BACKUP_DIR/latest.dump"
ln -sfn "backup-${stamp}.json" "$BACKUP_DIR/latest.json"
if [[ -n "$state_name" ]]; then
    ln -sfn "$state_name" "$BACKUP_DIR/latest-dashboard-state.tar.gz"
fi

monthly="$BACKUP_DIR/monthly"
mkdir -p "$monthly"
if [[ ! -f "$monthly/${month}-${POSTGRES_DB}.dump" ]]; then
    cp "$BACKUP_DIR/$dump_name" "$monthly/${month}-${POSTGRES_DB}.dump"
fi
if [[ -n "$state_name" && ! -f "$monthly/${month}-dashboard-state.tar.gz" ]]; then
    cp "$BACKUP_DIR/$state_name" "$monthly/${month}-dashboard-state.tar.gz"
fi

log "pruning daily backups older than ${BACKUP_RETENTION_DAYS} days"
find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name '*.dump' -o -name '*.tar.gz' -o -name 'backup-*.json' \) \
    -mtime +"$BACKUP_RETENTION_DAYS" -delete

monthly_days=$((BACKUP_MONTHLY_RETENTION * 31))
log "pruning monthly backups older than ${BACKUP_MONTHLY_RETENTION} months"
find "$monthly" -maxdepth 1 -type f -mtime +"$monthly_days" -delete

if [[ -n "$BACKUP_REMOTE" ]]; then
    command -v rsync >/dev/null 2>&1 || die "BACKUP_REMOTE is set but rsync is missing"
    log "copying backups to $BACKUP_REMOTE"
    rsync -a --exclude '.backup.lock' --exclude '.tmp.*' \
        "$BACKUP_DIR"/ "$BACKUP_REMOTE"/
fi

log "backup complete: $BACKUP_DIR/$dump_name"
if [[ -n "$state_name" ]]; then
    log "dashboard state: $BACKUP_DIR/$state_name"
fi

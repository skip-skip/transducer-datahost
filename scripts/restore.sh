#!/usr/bin/env bash
# Restore a database dump or the dashboard state volume.
#
#   scripts/restore.sh --list BACKUP.dump
#   scripts/restore.sh --database BACKUP.dump [--target transducer_restore]
#   scripts/restore.sh --database BACKUP.dump --production --force
#   scripts/restore.sh --dashboard-state BACKUP.tar.gz --force
#
# A normal --database restore creates a scratch database and leaves production
# alone. Restoring over POSTGRES_DB requires --production --force and stops the
# API, worker, and dashboard first. See docs/operations.md for the full drill.
set -euo pipefail

# shellcheck source=scripts/_common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_repo

usage() {
    cat <<'EOF'
Usage:
  scripts/restore.sh --list BACKUP.dump
  scripts/restore.sh --database BACKUP.dump [--target NAME]
  scripts/restore.sh --database BACKUP.dump --production --force
  scripts/restore.sh --dashboard-state BACKUP.tar.gz --force

A normal --database restore creates a scratch database and leaves production
alone. Restoring over POSTGRES_DB requires --production --force and stops the
API, worker, and dashboard first. See docs/operations.md for the full drill.
EOF
}

mode=""
source_path=""
target=""
production=false
force=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --list | --database | --dashboard-state)
            mode=${1#--}
            shift
            [[ $# -gt 0 ]] || die "$mode requires a file argument"
            source_path=$1
            ;;
        --target)
            shift
            [[ $# -gt 0 ]] || die "--target requires a database name"
            target=$1
            ;;
        --production)
            production=true
            ;;
        --force)
            force=true
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1 (use --help)"
            ;;
    esac
    shift
done

[[ -n "$mode" ]] || {
    usage
    exit 2
}
[[ -f "$source_path" ]] || die "file not found: $source_path"

POSTGRES_DB=$(setting POSTGRES_DB transducer)
POSTGRES_USER=$(setting POSTGRES_USER transducer_admin)

restore_database() {
    local db=$1
    compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
        pg_restore -h 127.0.0.1 -U "$POSTGRES_USER" -d "$db" \
        --no-owner --clean --if-exists --single-transaction <"$source_path"
}

database_counts() {
    local db=$1
    compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
        psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$db" -At -v ON_ERROR_STOP=1 -c \
        "SELECT 'import_file=' || count(*) FROM transducer.import_file;
         SELECT 'reading=' || count(*) FROM transducer.reading;"
}

case "$mode" in
    list)
        log "listing $source_path"
        compose exec -T db pg_restore --list <"$source_path"
        ;;

    database)
        POSTGRES_PASSWORD=$(require_setting POSTGRES_PASSWORD)
        target=${target:-transducer_restore}
        [[ "$target" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] \
            || die "--target must be a simple SQL identifier"

        if [[ "$target" == "$POSTGRES_DB" ]]; then
            [[ "$production" == true ]] \
                || die "refusing to overwrite $POSTGRES_DB without --production"
            [[ "$force" == true ]] \
                || die "refusing to overwrite $POSTGRES_DB without --force"
            log "restoring over production database $POSTGRES_DB"
            log "stopping API, worker, and dashboard"
            compose stop ingest-api ingest-worker read-api dashboard
            if ! restore_database "$POSTGRES_DB"; then
                warn "restore failed; the previous database state was rolled back"
                log "starting services"
                compose up -d --no-deps ingest-api ingest-worker read-api dashboard
                exit 1
            fi
            database_counts "$POSTGRES_DB"
            log "starting services"
            compose up -d --no-deps ingest-api ingest-worker read-api dashboard
            log "production restore complete; verify uploads and the dashboard"
            exit 0
        fi

        if [[ "$production" == true ]]; then
            die "--production only applies when --target is $POSTGRES_DB"
        fi

        log "recreating scratch database $target"
        compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
            psql -h 127.0.0.1 -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
            -c "DROP DATABASE IF EXISTS \"$target\" WITH (FORCE);" \
            -c "CREATE DATABASE \"$target\";"
        log "restoring into $target"
        restore_database "$target"
        database_counts "$target"
        log "scratch restore complete: database $target"
        ;;

    dashboard-state)
        [[ "$force" == true ]] \
            || die "restoring dashboard state deletes the current volume; pass --force"
        tar -tzf "$source_path" | grep -Eq '^(\./)?state\.json$' \
            || die "archive does not contain state.json"

        archive=$(realpath "$source_path")
        log "stopping dashboard"
        compose stop dashboard >/dev/null 2>&1 || true
        log "replacing dashboard state from $archive"
        compose run --rm --no-deps -u root \
            -v "$archive:/restore.tar.gz:ro" --entrypoint sh dashboard -c '
                set -eu
                find /var/lib/transducer-dashboard -mindepth 1 -maxdepth 1 \
                    -exec rm -rf {} +
                tar -xzf /restore.tar.gz -C /var/lib/transducer-dashboard
                chown -R 10001:10001 /var/lib/transducer-dashboard
            '
        log "starting dashboard"
        compose up -d --no-deps dashboard
        log "dashboard state restore complete"
        ;;
esac

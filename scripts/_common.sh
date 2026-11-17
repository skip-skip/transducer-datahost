#!/usr/bin/env bash
# Shared helpers for the operational scripts. This file is sourced, not run.
#
# Every script resolves the repository root from its own location, so it can be
# launched from any working directory (including a systemd unit).

: "${ROOT_DIR:=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)}"
export ROOT_DIR

# Docker Compose v2. Override COMPOSE_BIN to use `docker-compose` or a wrapper.
read -r -a COMPOSE_CMD <<< "${COMPOSE_BIN:-docker compose}"

timestamp() {
    date -u +%Y-%m-%dT%H:%M:%SZ
}

log() {
    printf '%s %s\n' "$(timestamp)" "$*"
}

warn() {
    log "WARN: $*" >&2
}

die() {
    log "ERROR: $*" >&2
    exit 1
}

# Read KEY from the environment first, then from .env, then return the default.
# Values are never executed, so passwords containing shell metacharacters are
# safe. Surrounding single or double quotes are stripped.
setting() {
    local key=$1 default=${2:-} line value
    if [[ -n "${!key:-}" ]]; then
        printf '%s' "${!key}"
        return 0
    fi
    if [[ -f "$ROOT_DIR/.env" ]]; then
        line=$(grep -E "^[[:space:]]*${key}=" "$ROOT_DIR/.env" | tail -n 1 || true)
        if [[ -n "$line" ]]; then
            value=${line#*=}
            if [[ ${#value} -ge 2 && ${value:0:1} == '"' && ${value: -1} == '"' ]]; then
                value=${value:1:${#value}-2}
            elif [[ ${#value} -ge 2 && ${value:0:1} == "'" && ${value: -1} == "'" ]]; then
                value=${value:1:${#value}-2}
            fi
            if [[ -n "$value" ]]; then
                printf '%s' "$value"
                return 0
            fi
        fi
    fi
    printf '%s' "$default"
}

require_setting() {
    local key=$1 value
    value=$(setting "$key")
    if [[ -z "$value" ]]; then
        die "$key is not set (add it to .env or the environment)"
    fi
    printf '%s' "$value"
}

require_repo() {
    [[ -f "$ROOT_DIR/docker-compose.yml" ]] \
        || die "$ROOT_DIR does not look like the transducer_dash checkout"
}

compose() {
    "${COMPOSE_CMD[@]}" "$@"
}

# Run psql in the db container and return an unaligned, tuples-only value.
psql_scalar() {
    local sql=$1
    compose exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" db \
        psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        -At -v ON_ERROR_STOP=1 -c "$sql"
}

# Print the container health status: running/healthy, running/unhealthy, etc.
container_status() {
    local container=$1 state health
    state=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null) || return 1
    health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        "$container" 2>/dev/null) || health=none
    printf '%s/%s' "$state" "$health"
}

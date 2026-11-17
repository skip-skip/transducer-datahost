#!/usr/bin/env bash
# Check the running stack and optionally post a webhook alert.
#
#   scripts/healthcheck.sh
#
# Exit 0 when every check passes or only warnings are present, 1 when a check
# fails. Intended for a systemd timer (deploy/systemd/transducer-healthcheck.*)
# or cron. See docs/operations.md for the signal list and alert configuration.
set -uo pipefail

# shellcheck source=scripts/_common.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

require_repo

FAILURES=()
WARNINGS=()

record_fail() {
    FAILURES+=("$*")
    printf 'FAIL %s\n' "$*" >&2
}

record_warn() {
    WARNINGS+=("$*")
    printf 'WARN %s\n' "$*" >&2
}

record_ok() {
    printf 'OK   %s\n' "$*"
}

POSTGRES_DB=$(setting POSTGRES_DB transducer)
POSTGRES_USER=$(setting POSTGRES_USER transducer_admin)
POSTGRES_PASSWORD=$(setting POSTGRES_PASSWORD "")
WORKER_HEARTBEAT_TIMEOUT_SECONDS=$(setting WORKER_HEARTBEAT_TIMEOUT_SECONDS 60)
HEALTHCHECK_MAX_INGEST_AGE_HOURS=$(setting HEALTHCHECK_MAX_INGEST_AGE_HOURS 168)
HEALTHCHECK_BACKLOG_WARN_SECONDS=$(setting HEALTHCHECK_BACKLOG_WARN_SECONDS 3600)
HEALTHCHECK_BACKLOG_FAIL_SECONDS=$(setting HEALTHCHECK_BACKLOG_FAIL_SECONDS 21600)
BACKUP_DIR=$(setting BACKUP_DIR /var/backups/transducer)
DOCKER_ROOT=$(setting DOCKER_ROOT /var/lib/docker)
TLS_CERT=$(setting TLS_CERT "$ROOT_DIR/certs/server.crt")

printf 'transducer healthcheck %s\n' "$(timestamp)"

# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

containers=(
    transducer-db
    transducer-ingest-api
    transducer-ingest-worker
    transducer-read-api
    transducer-dashboard
    transducer-caddy
)

for container in "${containers[@]}"; do
    if ! status=$(container_status "$container"); then
        record_fail "$container: container is missing"
        continue
    fi
    case "$status" in
        running/healthy) record_ok "$container: $status" ;;
        running/none) record_ok "$container: $status (no healthcheck)" ;;
        running/starting) record_warn "$container: health is still starting" ;;
        running/unhealthy) record_fail "$container: unhealthy" ;;
        running/*) record_warn "$container: $status" ;;
        *) record_fail "$container: $status" ;;
    esac
done

# ---------------------------------------------------------------------------
# HTTP health endpoints (from inside each container, no host port required)
# ---------------------------------------------------------------------------

HTTP_PROBE='import http.client; c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=5); c.request("GET", "/health"); r = c.getresponse(); print(r.status); print(r.read().decode("utf-8", "replace"))'

http_check() {
    local container=$1 label=$2 out status body
    if ! out=$(docker exec "$container" python -c "$HTTP_PROBE" 2>&1); then
        record_fail "$label: request failed: ${out//$'\n'/ }"
        return
    fi
    if [[ "$out" == *$'\n'* ]]; then
        status=${out%%$'\n'*}
        body=${out#*$'\n'}
    else
        status=$out
        body=""
    fi
    if [[ "$status" == 200 ]]; then
        record_ok "$label: HTTP 200"
    else
        record_fail "$label: HTTP $status ${body//$'\n'/ }"
    fi
}

for probe in "transducer-ingest-api:ingest-api" \
    "transducer-read-api:read-api" \
    "transducer-dashboard:dashboard"; do
    container=${probe%%:*}
    label=${probe#*:}
    status=$(container_status "$container" 2>/dev/null || true)
    if [[ "$status" == running/* ]]; then
        http_check "$container" "$label"
    fi
done

# ---------------------------------------------------------------------------
# Database signals
# ---------------------------------------------------------------------------

query() {
    local sql=$1 value
    if ! value=$(psql_scalar "$sql" 2>&1); then
        record_fail "database: query failed: ${value//$'\n'/ }"
        return 1
    fi
    printf '%s' "$value"
}

if [[ -z "$POSTGRES_PASSWORD" ]]; then
    record_fail "database: POSTGRES_PASSWORD is not set"
elif [[ "$(container_status transducer-db 2>/dev/null || true)" != running/* ]]; then
    record_warn "database: skipped because the db container is not running"
else
    heartbeat_age=$(query "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(last_seen)), -1)::bigint FROM ingest.worker_heartbeat;") || true
    if [[ -n "${heartbeat_age:-}" ]]; then
        if (( heartbeat_age < 0 )); then
            record_warn "worker heartbeat: none recorded"
        elif (( heartbeat_age > WORKER_HEARTBEAT_TIMEOUT_SECONDS )); then
            record_fail "worker heartbeat: ${heartbeat_age}s old (limit ${WORKER_HEARTBEAT_TIMEOUT_SECONDS}s)"
        else
            record_ok "worker heartbeat: ${heartbeat_age}s old"
        fi
    fi

    failed_jobs=$(query "SELECT count(*) FROM ingest.job WHERE status = 'failed' AND COALESCE(finished_at, received_at) > now() - interval '24 hours';") || true
    if [[ -n "${failed_jobs:-}" ]]; then
        if (( failed_jobs > 0 )); then
            record_fail "ingest: ${failed_jobs} failed job(s) in the last 24 hours"
        else
            record_ok "ingest: no failed jobs in the last 24 hours"
        fi
    fi

    backlog_age=$(query "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(received_at) FILTER (WHERE status IN ('queued', 'processing'))), 0)::bigint FROM ingest.job;") || true
    if [[ -n "${backlog_age:-}" ]]; then
        if (( backlog_age > HEALTHCHECK_BACKLOG_FAIL_SECONDS )); then
            record_fail "ingest backlog: oldest queued job is ${backlog_age}s old"
        elif (( backlog_age > HEALTHCHECK_BACKLOG_WARN_SECONDS )); then
            record_warn "ingest backlog: oldest queued job is ${backlog_age}s old"
        else
            record_ok "ingest backlog: ${backlog_age}s old"
        fi
    fi

    ingest_age=$(query "SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(imported_at)), -1)::bigint FROM transducer.import_file;") || true
    max_ingest_seconds=$((HEALTHCHECK_MAX_INGEST_AGE_HOURS * 3600))
    if [[ -n "${ingest_age:-}" ]]; then
        if (( ingest_age < 0 )); then
            record_warn "latest ingest: no import_file rows"
        elif (( ingest_age > max_ingest_seconds )); then
            record_warn "latest ingest: ${ingest_age}s old (threshold ${HEALTHCHECK_MAX_INGEST_AGE_HOURS}h; uploads are manual)"
        else
            record_ok "latest ingest: ${ingest_age}s old"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Disk usage
# ---------------------------------------------------------------------------

disk_check() {
    local path=$1 label=$2 used
    if [[ ! -e "$path" ]]; then
        record_warn "$label: $path not found"
        return
    fi
    used=$(df -P "$path" | awk 'NR == 2 { gsub(/%/, "", $5); print $5 }')
    if [[ -z "$used" ]]; then
        record_warn "$label: cannot read disk usage for $path"
    elif (( used >= 90 )); then
        record_fail "$label: ${used}% used at $path"
    elif (( used >= 80 )); then
        record_warn "$label: ${used}% used at $path"
    else
        record_ok "$label: ${used}% used at $path"
    fi
}

disk_check "$DOCKER_ROOT" "docker disk"
disk_check "$BACKUP_DIR" "backup disk"

# ---------------------------------------------------------------------------
# TLS certificate
# ---------------------------------------------------------------------------

if [[ ! -f "$TLS_CERT" ]]; then
    record_warn "certificate: $TLS_CERT not found"
elif ! command -v openssl >/dev/null 2>&1; then
    record_warn "certificate: openssl is not installed; cannot check expiry"
elif ! openssl x509 -checkend $((14 * 86400)) -noout -in "$TLS_CERT" >/dev/null 2>&1; then
    record_fail "certificate: expires within 14 days or is invalid ($TLS_CERT)"
elif ! openssl x509 -checkend $((30 * 86400)) -noout -in "$TLS_CERT" >/dev/null 2>&1; then
    record_warn "certificate: expires within 30 days ($TLS_CERT)"
else
    record_ok "certificate: valid for at least 30 days"
fi

# ---------------------------------------------------------------------------
# Optional alert
# ---------------------------------------------------------------------------

ALERT_WEBHOOK_URL=$(setting ALERT_WEBHOOK_URL "")
ALERT_ON_WARNINGS=$(setting ALERT_ON_WARNINGS false)
should_alert=false
if (( ${#FAILURES[@]} > 0 )); then
    should_alert=true
elif [[ "$ALERT_ON_WARNINGS" == true ]] && (( ${#WARNINGS[@]} > 0 )); then
    should_alert=true
fi

if [[ "$should_alert" == true && -n "$ALERT_WEBHOOK_URL" ]]; then
    text="transducer healthcheck on $(hostname) at $(timestamp): ${#FAILURES[@]} failure(s), ${#WARNINGS[@]} warning(s)"
    for line in "${FAILURES[@]}"; do
        text+=$'\n'"FAIL $line"
    done
    for line in "${WARNINGS[@]}"; do
        text+=$'\n'"WARN $line"
    done

    state_dir=$(setting HEALTHCHECK_STATE_DIR /var/lib/transducer-healthcheck)
    if ! mkdir -p "$state_dir" 2>/dev/null; then
        state_dir=${TMPDIR:-/tmp}
    fi
    state_file="$state_dir/last-alert"
    repeat_seconds=$(setting ALERT_REPEAT_SECONDS 3600)
    [[ "$repeat_seconds" =~ ^[0-9]+$ ]] || repeat_seconds=3600
    fingerprint=$(printf '%s\n' "${FAILURES[@]}" "${WARNINGS[@]}")
    payload_hash=$(printf '%s' "$fingerprint" | sha256sum | cut -d' ' -f1)
    now=$(date +%s)
    last_hash=""
    last_sent=0
    if [[ -f "$state_file" ]]; then
        read -r last_hash last_sent <"$state_file" || true
    fi
    [[ "$last_sent" =~ ^[0-9]+$ ]] || last_sent=0

    if [[ "$payload_hash" == "$last_hash" ]] \
        && (( now - last_sent < repeat_seconds )); then
        printf 'alert suppressed (unchanged, sent %ss ago)\n' "$((now - last_sent))"
    elif ! command -v curl >/dev/null 2>&1; then
        record_warn "alert: curl is not installed; webhook not sent"
    else
        payload=$(mktemp)
        if command -v python3 >/dev/null 2>&1; then
            python3 -c 'import json, sys; print(json.dumps({"text": sys.stdin.read()}))' \
                <<<"$text" >"$payload"
        elif command -v python >/dev/null 2>&1; then
            python -c 'import json, sys; print(json.dumps({"text": sys.stdin.read()}))' \
                <<<"$text" >"$payload"
        else
            printf 'WARN alert: python is required to encode the webhook payload\n'
            rm -f "$payload"
            payload=""
        fi
        if [[ -n "$payload" ]]; then
            if curl -fsS -m 10 -H 'Content-Type: application/json' \
                --data-binary @"$payload" "$ALERT_WEBHOOK_URL" >/dev/null; then
                printf '%s %s\n' "$payload_hash" "$now" >"$state_file"
                printf 'alert sent to webhook\n'
            else
                printf 'WARN alert: webhook delivery failed\n'
            fi
            rm -f "$payload"
        fi
    fi
fi

printf '\n%d failure(s), %d warning(s)\n' "${#FAILURES[@]}" "${#WARNINGS[@]}"
if (( ${#FAILURES[@]} > 0 )); then
    exit 1
fi
exit 0

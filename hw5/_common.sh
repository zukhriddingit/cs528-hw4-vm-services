#!/usr/bin/env bash
# Shared constants and helpers for CS528 HW5.
# Project ID is intentionally hardcoded for the grader.
PROJECT_ID="quick-formula-457319-q3"
REGION="us-central1"
ZONE="us-central1-a"
BUCKET_NAME="cloud_hw2_bucket"
SQL_INSTANCE="hw5-sql"
DB_NAME="hw5_requests"
DB_USER="hw5_user"
SECRET_ID="hw5-db-password"
SERVER_VM="hw5-server"
REPORTER_VM="hw5-reporter"
IP_NAME="hw5-server-ip"
FIREWALL_NAME="hw5-allow-web"
TOPIC_ID="hw3-forbidden-requests"
SUBSCRIPTION_ID="hw5-forbidden-sub"
GUARD_FUNCTION="hw5-sql-guard"
SCHEDULER_JOB="hw5-hourly-sql-stop"
WEB_SA="hw5-web-sa@${PROJECT_ID}.iam.gserviceaccount.com"
REPORTER_SA="hw5-reporter-sa@${PROJECT_ID}.iam.gserviceaccount.com"
GUARD_SA="hw5-guard-sa@${PROJECT_ID}.iam.gserviceaccount.com"
SCHEDULER_SA="hw5-scheduler-sa@${PROJECT_ID}.iam.gserviceaccount.com"
HW5_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Retry read-only API checks and waits; do not blindly retry resource creation.
gcloud_check_retry() {
    local attempt
    for attempt in 1 2 3; do
        if "$@"; then return 0; fi
        if [[ "$attempt" -lt 3 ]]; then
            echo "Google Cloud request failed; retrying ($attempt/3)..." >&2
            sleep 4
        fi
    done
    return 1
}

# Return 0 if present; 1 ONLY for a confirmed 404; 2 for unknown/API errors.
# Critically: DNS failures must never be interpreted as "database missing".
sql_exists() {
    local output
    if output="$(gcloud sql instances describe "$SQL_INSTANCE" \
        --project="$PROJECT_ID" --format='value(name)' 2>&1)"; then
        return 0
    fi
    if [[ "$output" == *"HTTPError 404"* || \
          "$output" == *"NOT_FOUND"* || \
          "$output" == *"was not found"* || \
          "$output" == *"does not exist"* ]]; then
        return 1
    fi
    printf 'ERROR: Could not determine Cloud SQL existence (not a confirmed 404):\n%s\n' \
        "$output" >&2
    return 2
}

wait_sql_operations() {
    local exists_rc=0 op
    sql_exists || exists_rc=$?
    case "$exists_rc" in
        0) ;;
        1) return 0 ;;
        *) echo 'ERROR: Cannot inspect Cloud SQL operations while the API is unavailable.' >&2; return 1 ;;
    esac
    while :; do
        op="$(gcloud sql operations list --project="$PROJECT_ID" --instance="$SQL_INSTANCE" \
            --filter='status!=DONE' --limit=1 --sort-by='~insertTime' \
            --format='value(name)')" || return 1
        [[ -n "$op" ]] || break
        echo "Waiting for Cloud SQL operation: $op" >&2
        gcloud_check_retry gcloud sql operations wait "$op" --project="$PROJECT_ID" \
            --timeout=unlimited || return 1
    done
}

sql_operation() {
    local message="$1"
    shift
    local op
    echo "$message" >&2
    # This is a MUTATING request. Do not retry it automatically if its outcome is unknown.
    op="$("$@" --async --format='value(name)')" || {
        echo "ERROR: Could not confirm whether Cloud SQL action started: $message" >&2
        return 1
    }
    if [[ -z "$op" ]]; then
        echo "ERROR: No Cloud SQL operation ID was returned for $message" >&2
        return 1
    fi
    gcloud_check_retry gcloud sql operations wait "$op" --project="$PROJECT_ID" \
        --timeout=unlimited
}

sql_stop() {
    local exists_rc=0 state policy
    sql_exists || exists_rc=$?
    case "$exists_rc" in
        0) ;;
        1) echo 'Cloud SQL instance does not exist; nothing to stop.'; return 0 ;;
        *) echo 'CRITICAL: Cannot confirm Cloud SQL state. Do not assume it is stopped!' >&2; return 1 ;;
    esac
    wait_sql_operations || return 1
    policy="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
        --format='value(settings.activationPolicy)')" || return 1
    if [[ "$policy" != 'NEVER' ]]; then
        sql_operation "Stopping Cloud SQL $SQL_INSTANCE" \
            gcloud sql instances patch "$SQL_INSTANCE" --project="$PROJECT_ID" \
            --activation-policy=NEVER --quiet || return 1
    fi
    state="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
        --format='value(state)')" || return 1
    policy="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
        --format='value(settings.activationPolicy)')" || return 1
    printf 'Cloud SQL: state=%s activationPolicy=%s\n' "$state" "$policy"
    [[ "$state" == 'STOPPED' && "$policy" == 'NEVER' ]]
}

ensure_sa() {
    local name="$1"
    if ! gcloud iam service-accounts describe \
      "${name}@${PROJECT_ID}.iam.gserviceaccount.com" --project="$PROJECT_ID" >/dev/null 2>&1; then
        gcloud iam service-accounts create "$name" --project="$PROJECT_ID" \
          --display-name="CS528 HW5 $name"
    fi
}

#!/usr/bin/env bash
# CS528 HW6 — lossless HW5-to-3NF migration with Cloud SQL operation-safe cleanup.
# Run from the Cloud HWs root: bash hw6/step2_migrate.sh
set -Eeuo pipefail

PROJECT_ID="quick-formula-457319-q3"
INSTANCE="hw5-sql"
SECRET="hw5-db-password"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="$ROOT/hw5/.venv/bin/python"
OUTPUT="$HERE/step2_3nf_output.txt"
START_REQUESTED=0

# Wait for operations before submitting any NEW patch (avoids HTTP 409).
# A nonzero wait/lookup means the state is not safe to assume.
wait_for_pending() {
    local pending op
    pending="$(gcloud sql operations list \
        --project="$PROJECT_ID" --instance="$INSTANCE" \
        --filter='status!=DONE' --format='value(name)')" || return 1
    if [[ -z "$pending" ]]; then return 0; fi
    while IFS= read -r op; do
        [[ -n "$op" ]] || continue
        printf 'Waiting for Cloud SQL operation %s to finish...\n' "$op" >&2
        gcloud sql operations wait "$op" --project="$PROJECT_ID" \
            --timeout=unlimited || return 1
    done <<< "$pending"
    # Confirm none remain, because external processes may have started another.
    pending="$(gcloud sql operations list \
        --project="$PROJECT_ID" --instance="$INSTANCE" \
        --filter='status!=DONE' --format='value(name)')" || return 1
    [[ -z "$pending" ]]
}

sql_status() {
    local status
    status="$(gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
        --format='value(state,settings.activationPolicy)')" || return 1
    # gcloud prints tab-separated columns.
    read -r SQL_STATE SQL_POLICY <<< "$status"
    [[ -n "$SQL_STATE" && -n "$SQL_POLICY" ]]
}

# Never submit a second patch while another Cloud SQL operation is in progress.
# Clean up even if the Python profiler fails or an API call times out.
cleanup() {
    local rc=$? stop_operation
    trap - EXIT INT TERM
    if [[ "$START_REQUESTED" == "1" ]]; then
        echo 'Cleanup: checking pending operations before stopping Cloud SQL...' >&2
        if ! wait_for_pending; then
            echo 'CRITICAL: Could not confirm all operations finished; do NOT assume SQL is stopped.' >&2
            rc=1
        elif ! sql_status; then
            echo 'CRITICAL: Could not read Cloud SQL status.' >&2
            rc=1
        elif [[ "$SQL_STATE" == 'STOPPED' && "$SQL_POLICY" == 'NEVER' ]]; then
            echo 'Cloud SQL is already STOPPED / NEVER.' >&2
        else
            echo 'Cleanup: stopping Cloud SQL...' >&2
            # --async gives us an operation ID so the CLI cannot time out mid-patch.
            if stop_operation="$(gcloud sql instances patch "$INSTANCE" \
                    --project="$PROJECT_ID" --activation-policy=NEVER \
                    --async --quiet --format='value(name)')"; then
                if [[ -n "$stop_operation" ]]; then
                    if ! gcloud sql operations wait "$stop_operation" \
                            --project="$PROJECT_ID" --timeout=unlimited; then
                        echo "CRITICAL: Stop operation $stop_operation did not complete successfully." >&2
                        rc=1
                    fi
                else
                    echo 'CRITICAL: Stop patch returned no operation ID. Verify state manually.' >&2
                    rc=1
                fi
            else
                echo 'CRITICAL: Cloud SQL stop patch failed. Verify state manually.' >&2
                rc=1
            fi
        fi
    fi
    if sql_status; then
        printf 'Final SQL: state=%s activationPolicy=%s\n' "$SQL_STATE" "$SQL_POLICY" >&2
        if [[ "$START_REQUESTED" == "1" && ( "$SQL_STATE" != 'STOPPED' || "$SQL_POLICY" != 'NEVER' ) ]]; then
            echo 'CRITICAL: Database is not confirmed stopped. Investigate immediately.' >&2
            rc=1
        fi
    else
        echo 'CRITICAL: Cannot verify the Cloud SQL final state.' >&2
        rc=1
    fi
    exit "$rc"
}

if [[ ! -x "$PYTHON" ]]; then
    echo "Missing HW5 Python environment at $PYTHON" >&2
    exit 1
fi
"$PYTHON" -c 'import google.auth, google.cloud.sql.connector, pg8000' || {
    echo 'Missing Python dependencies in hw5/.venv.' >&2
    exit 1
}

if [[ -s "$ROOT/hw5/.db_password" ]]; then
    DB_PASSWORD="$(cat "$ROOT/hw5/.db_password")"
else
    echo 'Reading saved database password from Secret Manager...' >&2
    DB_PASSWORD="$(gcloud secrets versions access latest \
        --secret="$SECRET" --project="$PROJECT_ID")" || exit 1
fi
[[ -n "$DB_PASSWORD" ]] || { echo 'Database password was empty.' >&2; exit 1; }

if ! sql_status; then
    echo 'Could not determine current SQL state; no changes made.' >&2
    exit 1
fi
printf 'Initial SQL: state=%s activationPolicy=%s\n' "$SQL_STATE" "$SQL_POLICY"
if [[ "$SQL_STATE" != 'STOPPED' || "$SQL_POLICY" != 'NEVER' ]]; then
    echo 'Safety stop: expected STOPPED / NEVER before starting.' >&2
    echo 'Finish any other workloads and stop Cloud SQL before retrying.' >&2
    exit 1
fi
if ! wait_for_pending; then
    echo 'Could not verify pending SQL operations; no new patch submitted.' >&2
    exit 1
fi

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Set this BEFORE the patch: if the request succeeds but the CLI exits badly,
# cleanup must still assess instance state and prevent unnecessary billing.
START_REQUESTED=1
printf 'Starting Cloud SQL asynchronously; waiting for completion...\n'
start_operation="$(gcloud sql instances patch "$INSTANCE" \
    --project="$PROJECT_ID" --activation-policy=ALWAYS \
    --async --quiet --format='value(name)')"
[[ -n "$start_operation" ]] || { echo 'Start patch returned no operation ID.' >&2; exit 1; }
gcloud sql operations wait "$start_operation" --project="$PROJECT_ID" --timeout=unlimited

if ! sql_status || [[ "$SQL_STATE" != 'RUNNABLE' || "$SQL_POLICY" != 'ALWAYS' ]]; then
    echo "Cloud SQL did not reach RUNNABLE / ALWAYS." >&2
    exit 1
fi
printf 'Cloud SQL is RUNNABLE. Running 3NF migration...\n'
HW6_DB_PASSWORD="$DB_PASSWORD" "$PYTHON" "$HERE/step2_migrate.py" | tee "$OUTPUT"
unset DB_PASSWORD
printf 'Migration log saved to: %s\n' "$OUTPUT"

#!/usr/bin/env bash
# CS528 HW6: provision a VM, train two models, upload predictions,
# print result files from Cloud Storage, delete VM, stop Cloud SQL.
# Run from anywhere: bash hw6/step3_run_vm.sh
set -Eeuo pipefail

PROJECT_ID='quick-formula-457319-q3'
ZONE='us-central1-a'
SQL_INSTANCE='hw5-sql'
DB_SECRET='hw5-db-password'
BUCKET='cloud_hw2_bucket'
SA_ID='hw6-ml-sa'
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
MACHINE="${HW6_MACHINE:-e2-standard-2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_ID="$(date -u '+%Y%m%dT%H%M%SZ')"
VM_NAME="hw6-ml-$(date -u '+%m%d%H%M%S')"
PREFIX="hw6/runs/$RUN_ID"
STATUS_URI="gs://${BUCKET}/${PREFIX}/status.json"
METRICS_URI="gs://${BUCKET}/${PREFIX}/metrics.json"
SQL_STARTED=0
VM_CREATION_ATTEMPTED=0

sql_status() {
    local raw
    raw="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
         --format='value(state,settings.activationPolicy)')" || return 1
    read -r SQL_STATE SQL_POLICY <<< "$raw"
    [[ -n "$SQL_STATE" && -n "$SQL_POLICY" ]]
}

wait_sql_operations() {
    local operations op
    while :; do
        operations="$(gcloud sql operations list --project="$PROJECT_ID" \
           --instance="$SQL_INSTANCE" --filter='status!=DONE' \
           --format='value(name)')" || return 1
        [[ -n "$operations" ]] || return 0
        while IFS= read -r op; do
            [[ -n "$op" ]] || continue
            echo "Waiting for Cloud SQL operation $op..."
            gcloud sql operations wait "$op" --project="$PROJECT_ID" --timeout=unlimited || return 1
        done <<< "$operations"
    done
}

cleanup() {
    local rc=$? op
    trap - EXIT INT TERM
    set +e
    echo
    echo '=== Automatic HW6 cleanup ==='
    # Delete VM FIRST to end all DB connections and stop VM billing.
    if [[ "$VM_CREATION_ATTEMPTED" == 1 ]]; then
        if gcloud compute instances describe "$VM_NAME" --project="$PROJECT_ID" \
                --zone="$ZONE" >/dev/null 2>&1; then
            echo "Deleting VM $VM_NAME..."
            gcloud compute instances delete "$VM_NAME" --project="$PROJECT_ID" \
                --zone="$ZONE" --quiet || {
                echo 'CRITICAL: Could not delete the VM; check Compute Engine.' >&2
                rc=1
            }
            if gcloud compute instances describe "$VM_NAME" --project="$PROJECT_ID" \
                    --zone="$ZONE" >/dev/null 2>&1; then
                echo 'CRITICAL: VM remains after deletion request.' >&2
                rc=1
            fi
        else
            local check_output
            check_output="$(gcloud compute instances describe "$VM_NAME" --project="$PROJECT_ID" --zone="$ZONE" 2>&1)"
            if [[ "$check_output" == *'was not found'* || "$check_output" == *'NOT_FOUND'* || "$check_output" == *'404'* ]]; then
                echo "VM $VM_NAME does not exist (no VM charges)."
            else
                echo "CRITICAL: Could not confirm VM $VM_NAME is absent: $check_output" >&2
                rc=1
            fi
        fi
    fi
    if [[ "$SQL_STARTED" == 1 ]]; then
        echo 'Waiting for Cloud SQL operations before stop...'
        if wait_sql_operations && sql_status; then
            if [[ "$SQL_STATE" != 'STOPPED' || "$SQL_POLICY" != 'NEVER' ]]; then
                echo 'Stopping Cloud SQL asynchronously...'
                op="$(gcloud sql instances patch "$SQL_INSTANCE" --project="$PROJECT_ID" \
                       --activation-policy=NEVER --async --quiet --format='value(name)')"
                if [[ -n "$op" ]]; then
                    gcloud sql operations wait "$op" --project="$PROJECT_ID" \
                        --timeout=unlimited || rc=1
                else
                    echo 'CRITICAL: Could not obtain SQL stop operation ID.' >&2
                    rc=1
                fi
            fi
        else
            echo 'CRITICAL: Cannot verify existing Cloud SQL operations/state.' >&2
            rc=1
        fi
    fi
    if sql_status; then
        printf 'Final SQL: state=%s activationPolicy=%s\n' "$SQL_STATE" "$SQL_POLICY"
        [[ "$SQL_STATE" == STOPPED && "$SQL_POLICY" == NEVER ]] || rc=1
    else
        echo 'CRITICAL: Cannot verify final Cloud SQL status.' >&2
        rc=1
    fi
    echo 'Verify the VM is deleted and SQL is STOPPED / NEVER before leaving GCP.'
    exit "$rc"
}

for cmd in gcloud python3; do
    command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }
done
for required in step3_models.py requirements_step3.txt step3_vm_startup.sh; do
    [[ -s "$HERE/$required" ]] || { echo "Missing $HERE/$required" >&2; exit 1; }
done
# Preflight region, bucket and database BEFORE mutating compute state.
gcloud storage ls "gs://$BUCKET/" >/dev/null || { echo 'Cloud bucket inaccessible.' >&2; exit 1; }
sql_status || { echo 'Could not read Cloud SQL instance.' >&2; exit 1; }
if [[ "$SQL_STATE" != 'STOPPED' || "$SQL_POLICY" != 'NEVER' ]]; then
    printf 'Safety stop: initial SQL state=%s policy=%s, expected STOPPED / NEVER.\n' \
        "$SQL_STATE" "$SQL_POLICY" >&2
    exit 1
fi
wait_sql_operations
# The previous preflight omitted its EXIT trap, so catch leftover smoke VMs.
SMOKE_VMS="$(gcloud compute instances list --project="$PROJECT_ID" \
    --filter='name~hw6-smoke-' --format='value(name)')" || exit 1
if [[ -n "$SMOKE_VMS" ]]; then
    echo 'ERROR: An HW6 smoke-test VM still exists. Delete it before training.' >&2
    gcloud compute instances list --project="$PROJECT_ID" \
        --filter='name~hw6-smoke-' --format='table(name,zone,status)' >&2
    exit 1
fi
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
        --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "The VM name $VM_NAME already exists; stopping safely." >&2
    exit 1
fi

# Provision a dedicated VM identity and narrowly scoped data access.
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$SA_ID" --project="$PROJECT_ID" \
        --display-name='CS528 HW6 ML VM'
fi
printf 'Checking IAM permissions for VM service account...\n'
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role='roles/cloudsql.client' \
    --condition=None --quiet >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role='roles/logging.logWriter' \
    --condition=None --quiet >/dev/null
gcloud secrets add-iam-policy-binding "$DB_SECRET" --project="$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role='roles/secretmanager.secretAccessor' \
    --quiet >/dev/null
# New service accounts can be visible to IAM but not yet to Cloud Storage.
# Retry ONLY the specific propagation error; fail promptly for other errors.
grant_bucket_access() {
    local attempt output
    for attempt in $(seq 1 12); do
        if output="$(gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
            --member="serviceAccount:$SA_EMAIL" --role='roles/storage.objectUser' \
            --quiet 2>&1)"; then
            echo 'Cloud Storage IAM binding succeeded.'
            return 0
        fi
        if [[ "$output" == *'Service account'*'does not exist'* || \
              "$output" == *'service account'*'does not exist'* ]]; then
            if [[ "$attempt" -lt 12 ]]; then
                printf 'Cloud Storage has not recognized the new service account (attempt %s/12); retrying...\n' "$attempt" >&2
                sleep 15
                continue
            fi
        fi
        printf '%s\n' "$output" >&2
        echo 'Bucket permission grant failed. Cloud SQL and VM have NOT been started.' >&2
        return 1
    done
}
grant_bucket_access

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
python3 - "$HERE" "$TMP_DIR/hw6_job.zip" <<'PY'
import sys, zipfile
from pathlib import Path
source, dest = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(dest, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for name in ('step3_models.py', 'requirements_step3.txt'):
        archive.write(source / name, arcname=name)
PY
printf 'Uploading Python application bundle to gs://%s/%s/job.zip\n' "$BUCKET" "$PREFIX"
gcloud storage cp "$TMP_DIR/hw6_job.zip" "gs://$BUCKET/$PREFIX/job.zip" \
    --quiet
rm -rf "$TMP_DIR"
trap - EXIT

# Once database startup begins, always attempt cleanup, even on Ctrl-C.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
SQL_STARTED=1
printf 'Starting Cloud SQL (operation-safe; may take several minutes)...\n'
start_op="$(gcloud sql instances patch "$SQL_INSTANCE" --project="$PROJECT_ID" \
    --activation-policy=ALWAYS --async --quiet --format='value(name)')"
[[ -n "$start_op" ]] || { echo 'Cloud SQL start returned no operation ID.' >&2; exit 1; }
gcloud sql operations wait "$start_op" --project="$PROJECT_ID" --timeout=unlimited
sql_status
[[ "$SQL_STATE" == RUNNABLE && "$SQL_POLICY" == ALWAYS ]] || {
    echo "Database failed to reach RUNNABLE/ALWAYS: $SQL_STATE/$SQL_POLICY" >&2
    exit 1
}

printf 'Creating short-lived Compute Engine VM %s (%s)...\n' "$VM_NAME" "$MACHINE"
VM_CREATION_ATTEMPTED=1
gcloud compute instances create "$VM_NAME" --project="$PROJECT_ID" --zone="$ZONE" \
    --machine-type="$MACHINE" --image-family=debian-12 --image-project=debian-cloud \
    --service-account="$SA_EMAIL" --scopes=cloud-platform \
    --boot-disk-size=30GB --boot-disk-type=pd-balanced \
    --metadata="hw6-project=$PROJECT_ID,hw6-bucket=$BUCKET,hw6-run-id=$RUN_ID,hw6-sql-instance=$SQL_INSTANCE,hw6-secret=$DB_SECRET" \
    --metadata-from-file="startup-script=$HERE/step3_vm_startup.sh" \
    --quiet

echo "Waiting for VM training result in $STATUS_URI"
echo "Live progress: gs://$BUCKET/$PREFIX/progress.json"
echo "Live logs: gs://$BUCKET/$PREFIX/bootstrap.log, pip.log and models.log"
echo "Exact run prefix: gs://$BUCKET/$PREFIX/"
printf '%s\n' "gs://$BUCKET/$PREFIX/" > "$HERE/step3_last_run_prefix.txt"

show_run_diagnostics() {
    echo '=== VM job diagnostics from GCS (if available) ==='
    gcloud storage ls "gs://$BUCKET/$PREFIX/" || true
    local name uri
    for name in progress.json bootstrap.log pip.log models.log country_metrics.json metrics_partial.json status.json; do
        uri="gs://$BUCKET/$PREFIX/$name"
        if gcloud storage ls "$uri" >/dev/null 2>&1; then
            printf '\n--- %s ---\n' "$name"
            gcloud storage cat "$uri" | tail -n 55 || true
        else
            printf '\n--- %s NOT UPLOADED ---\n' "$name"
        fi
    done
    echo '=== VM serial startup markers (if VM exists) ==='
    gcloud compute instances get-serial-port-output "$VM_NAME" --project="$PROJECT_ID" \
        --zone="$ZONE" --port=1 --start=0 2>/dev/null | \
        grep -Ei 'HW6|metadata_script_runner|startup-script|no space|out of memory|killed process' | tail -n 55 || true
}
FINISHED=0
for poll in $(seq 1 120); do
    if gcloud storage ls "$STATUS_URI" >/dev/null 2>&1; then
        FINISHED=1
        break
    fi
    if (( poll % 6 == 0 )); then
        printf 'Still waiting for VM training: %s minutes elapsed...\n' "$(( poll / 3 ))"
        if gcloud storage ls "gs://$BUCKET/$PREFIX/progress.json" >/dev/null 2>&1; then
            gcloud storage cat "gs://$BUCKET/$PREFIX/progress.json" || true
        else
            echo 'No bootstrap progress marker yet.'
        fi
    fi
    sleep 20
done
if [[ "$FINISHED" != 1 ]]; then
    echo 'No VM completion marker after ~40 minutes; collecting diagnostics BEFORE deleting VM.' >&2
    show_run_diagnostics
    exit 1
fi

printf '\n=== VM status ===\n'
gcloud storage cat "$STATUS_URI"
SUCCESS="$(gcloud storage cat "$STATUS_URI" | \
    python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("success") else "0")')"
if [[ "$SUCCESS" != 1 ]]; then
    echo 'VM reported an error. Collecting stage and runtime logs:' >&2
    show_run_diagnostics
    exit 1
fi

printf '\n=== Metrics file read BACK from Cloud Storage ===\n'
gcloud storage cat "$METRICS_URI"
for name in country_test_predictions.csv income_test_predictions.csv; do
    printf '\n=== Cloud Storage file: %s (first 12 rows) ===\n' "$name"
    gcloud storage cat "gs://$BUCKET/$PREFIX/$name" | sed -n '1,13p'
done
printf '\nOutput bucket: gs://%s/%s/\n' "$BUCKET" "$PREFIX"
printf 'GitHub submission: commit the local hw6/ files after reviewing results.\n'
# Trap automatically deletes the VM and stops Cloud SQL.

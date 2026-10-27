#!/usr/bin/env bash
# Noninteractive, rerunnable grader cleanup. NEVER delete the SQL instance.
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

ERRORS=0
try() { "$@" || { echo "WARNING: Failed command: $*" >&2; ERRORS=$((ERRORS + 1)); }; }

# Avoid a scheduled function racing with our explicit SQL stop.
if gcloud scheduler jobs describe "$SCHEDULER_JOB" --project="$PROJECT_ID" \
    --location="$REGION" >/dev/null 2>&1; then
    try gcloud scheduler jobs pause "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --quiet
fi
if ! sql_stop; then
    echo 'CRITICAL: Cloud SQL stop not confirmed. Inspect instance and stop manually!' >&2
    ERRORS=$((ERRORS + 1))
fi

# Instances first, so their NICs no longer hold the reserved address.
for vm in "$SERVER_VM" "$REPORTER_VM"; do
    if gcloud compute instances describe "$vm" --project="$PROJECT_ID" \
        --zone="$ZONE" >/dev/null 2>&1; then
        try gcloud compute instances delete "$vm" --project="$PROJECT_ID" \
          --zone="$ZONE" --quiet
    fi
done
if gcloud compute addresses describe "$IP_NAME" --project="$PROJECT_ID" \
    --region="$REGION" >/dev/null 2>&1; then
    try gcloud compute addresses delete "$IP_NAME" --project="$PROJECT_ID" \
      --region="$REGION" --quiet
fi
if gcloud compute firewall-rules describe "$FIREWALL_NAME" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    try gcloud compute firewall-rules delete "$FIREWALL_NAME" \
      --project="$PROJECT_ID" --quiet
fi
if gcloud pubsub subscriptions describe "$SUBSCRIPTION_ID" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    try gcloud pubsub subscriptions delete "$SUBSCRIPTION_ID" \
      --project="$PROJECT_ID" --quiet
fi
if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
    --project="$PROJECT_ID" --location="$REGION" >/dev/null 2>&1; then
    try gcloud scheduler jobs delete "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --quiet
fi
if gcloud functions describe "$GUARD_FUNCTION" --gen2 --region="$REGION" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    try gcloud functions delete "$GUARD_FUNCTION" --gen2 --project="$PROJECT_ID" \
      --region="$REGION" --quiet
fi
# Keep Secret Manager password and HW5 service accounts: they are reusable and
# have negligible/no compute charges. The hourly job is no longer necessary
# because the database is verified STOPPED. HW3 shared topic is preserved.

printf '\nTeardown complete. Cloud SQL was preserved (and should be STOPPED).\n'
gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
    --format='table(name,state,settings.activationPolicy)' || true
if [[ "$ERRORS" -ne 0 ]]; then
    printf 'There were %s cleanup errors; investigate before leaving resources.\n' "$ERRORS" >&2
    exit 1
fi

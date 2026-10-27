#!/usr/bin/env bash
# Run ONLY immediately before a live load test, and resume afterwards.
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
gcloud scheduler jobs pause "$SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --quiet
printf 'Hourly database-stop job PAUSED. Remember: bash hw5/resume_guard.sh\n'

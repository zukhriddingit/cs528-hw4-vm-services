#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
gcloud scheduler jobs resume "$SCHEDULER_JOB" \
  --location="$REGION" --project="$PROJECT_ID" --quiet
printf 'Hourly database-stop job ENABLED.\n'

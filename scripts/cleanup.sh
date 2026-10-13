#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID=${PROJECT_ID:-quick-formula-457319-q3}
ZONE=${ZONE:-us-central1-a}
REGION=${ZONE%-*}
read -rp "DELETE HW4 VMs and release static IP in $PROJECT_ID? Type DELETE: " CONFIRM
[[ "$CONFIRM" == "DELETE" ]] || exit 1
for vm in hw4-server hw4-reporter hw4-client; do
  if gcloud compute instances describe "$vm" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances delete "$vm" --zone="$ZONE" --project="$PROJECT_ID" --quiet
  fi
done
if gcloud compute addresses describe hw4-server-ip --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud compute addresses delete hw4-server-ip --region="$REGION" --project="$PROJECT_ID" --quiet
fi
if gcloud compute firewall-rules describe hw4-allow-web --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud compute firewall-rules delete hw4-allow-web --project="$PROJECT_ID" --quiet
fi
if gcloud pubsub subscriptions describe hw4-forbidden-sub --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud pubsub subscriptions delete hw4-forbidden-sub --project="$PROJECT_ID" --quiet
fi
printf 'HW4 VMs, IP, firewall, and dedicated HW4 subscription removed. SAs and HW3 Pub/Sub topic retained.\n' 

#!/usr/bin/env bash
# Remove ONLY HW8 resources. Preserve HW2/HW3/HW4 assets and the shared service account.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:-quick-formula-457319-q3}
REGION=${REGION:-us-central1}
if [[ "${1:-}" != "--yes" ]]; then
  echo "This permanently deletes HW8 VMs and their disks, groups, firewall, health check, LB and IP."
  echo "It keeps HW2 pages, Pub/Sub, IAM service account, GitHub, and local experiment CSVs."
  echo "Run: bash scripts/cleanup.sh --yes"
  exit 1
fi
exists() { "$@" >/dev/null 2>&1; }

if exists gcloud compute forwarding-rules describe hw8-forward --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute forwarding-rules delete hw8-forward --project="$PROJECT_ID" --region="$REGION" --quiet
fi
if exists gcloud compute backend-services describe hw8-backend --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute backend-services delete hw8-backend --project="$PROJECT_ID" --region="$REGION" --quiet
fi
if exists gcloud compute health-checks describe hw8-health --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute health-checks delete hw8-health --project="$PROJECT_ID" --region="$REGION" --quiet
fi
for Z in a b; do
  ZONE="$REGION-$Z"
  if exists gcloud compute instance-groups unmanaged describe "hw8-ig-$Z" --project="$PROJECT_ID" --zone="$ZONE"; then
    gcloud compute instance-groups unmanaged delete "hw8-ig-$Z" --project="$PROJECT_ID" --zone="$ZONE" --quiet
  fi
  if exists gcloud compute instances describe "hw8-server-$Z" --project="$PROJECT_ID" --zone="$ZONE"; then
    gcloud compute instances delete "hw8-server-$Z" --project="$PROJECT_ID" --zone="$ZONE" --quiet
  fi
done
if exists gcloud compute addresses describe hw8-lb-ip --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute addresses delete hw8-lb-ip --project="$PROJECT_ID" --region="$REGION" --quiet
fi
if exists gcloud compute firewall-rules describe hw8-allow-web --project="$PROJECT_ID"; then
  gcloud compute firewall-rules delete hw8-allow-web --project="$PROJECT_ID" --quiet
fi
echo "HW8 resources removed. Shared coursework resources and local evidence preserved."

#!/usr/bin/env bash
# Snapshot HW8 infrastructure before deleting live resources.
set -euo pipefail
ROOT=$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)
PROJECT_ID=\${PROJECT_ID:-quick-formula-457319-q3}
REGION=\${REGION:-us-central1}
OUT="$ROOT/results/cloud_config"
mkdir -p "$OUT"

gcloud compute instances list --project="$PROJECT_ID" --filter="name~hw8-server" --format=json > "$OUT/instances.json"
gcloud compute instance-groups unmanaged list --project="$PROJECT_ID" --format=json > "$OUT/instance_groups.json"
gcloud compute health-checks describe hw8-health --project="$PROJECT_ID" --region="$REGION" --format=json > "$OUT/health_check.json"
gcloud compute backend-services describe hw8-backend --project="$PROJECT_ID" --region="$REGION" --format=json > "$OUT/backend_service.json"
gcloud compute backend-services get-health hw8-backend --project="$PROJECT_ID" --region="$REGION" --format=json > "$OUT/backend_health.json"
gcloud compute forwarding-rules describe hw8-forward --project="$PROJECT_ID" --region="$REGION" --format=json > "$OUT/forwarding_rule.json"
gcloud compute addresses describe hw8-lb-ip --project="$PROJECT_ID" --region="$REGION" --format=json > "$OUT/forwarding_ip.json"
gcloud compute firewall-rules describe hw8-allow-web --project="$PROJECT_ID" --format=json > "$OUT/firewall_rule.json"
echo "Saved GCP HW8 configuration to $OUT"

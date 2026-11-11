#!/usr/bin/env bash
# CS528 HW8 - deploy two zonal web VMs behind a regional external passthrough NLB.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT_ID=${PROJECT_ID:-quick-formula-457319-q3}
REGION=${REGION:-us-central1}
BUCKET_NAME=${BUCKET_NAME:-cloud_hw2_bucket}
TOPIC_ID=${TOPIC_ID:-hw3-forbidden-requests}
WEB_SA=${WEB_SA:-hw4-web-sa@${PROJECT_ID}.iam.gserviceaccount.com}
PORT=8080

exists() { "$@" >/dev/null 2>&1; }
echo "Project: $PROJECT_ID | region: $REGION | backend VMs: hw8-server-a/b"
echo "WARNING: Running VMs, external IPv4 addresses and the forwarding rule incur GCP charges."
gcloud storage ls "gs://${BUCKET_NAME}/pages/0.html" >/dev/null
if ! exists gcloud iam service-accounts describe "$WEB_SA" --project="$PROJECT_ID"; then
  echo "Missing service account: $WEB_SA. Recreate/grant it as described in README." >&2
  exit 1
fi

if ! exists gcloud compute firewall-rules describe hw8-allow-web --project="$PROJECT_ID"; then
  gcloud compute firewall-rules create hw8-allow-web \
    --project="$PROJECT_ID" --network=default --allow=tcp:$PORT \
    --source-ranges=0.0.0.0/0 --target-tags=hw8-web
fi

for Z in a b; do
  ZONE="$REGION-$Z"
  VM="hw8-server-$Z"
  IG="hw8-ig-$Z"

  if ! exists gcloud compute instances describe "$VM" --project="$PROJECT_ID" --zone="$ZONE"; then
    gcloud compute instances create "$VM" \
      --project="$PROJECT_ID" --zone="$ZONE" --machine-type=e2-micro \
      --image-family=debian-12 --image-project=debian-cloud \
      --boot-disk-size=10GB --boot-disk-type=pd-standard \
      --tags=hw8-web --service-account="$WEB_SA" --scopes=cloud-platform \
      --metadata="project-id=$PROJECT_ID,bucket-name=$BUCKET_NAME,topic-id=$TOPIC_ID" \
      --metadata-from-file="startup-script=$ROOT/scripts/startup-server.sh,server-code=$ROOT/server/app.py,server-requirements=$ROOT/server/requirements.txt"
  else
    echo "$VM already exists (startup metadata/source not overwritten)."
  fi

  if ! exists gcloud compute instance-groups unmanaged describe "$IG" --project="$PROJECT_ID" --zone="$ZONE"; then
    gcloud compute instance-groups unmanaged create "$IG" --project="$PROJECT_ID" --zone="$ZONE"
  fi
  if ! gcloud compute instance-groups unmanaged list-instances "$IG" \
    --project="$PROJECT_ID" --zone="$ZONE" --format="value(instance)" | grep -q "/instances/$VM$"; then
    gcloud compute instance-groups unmanaged add-instances "$IG" \
      --project="$PROJECT_ID" --zone="$ZONE" --instances="$VM"
  fi
done

if ! exists gcloud compute health-checks describe hw8-health --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute health-checks create http hw8-health \
    --project="$PROJECT_ID" --region="$REGION" \
    --port="$PORT" --request-path=/health --check-interval=5s \
    --timeout=2s --healthy-threshold=2 --unhealthy-threshold=2
fi

if ! exists gcloud compute backend-services describe hw8-backend --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute backend-services create hw8-backend \
    --project="$PROJECT_ID" --region="$REGION" \
    --load-balancing-scheme=EXTERNAL --protocol=TCP \
    --health-checks=hw8-health --health-checks-region="$REGION"
fi

for Z in a b; do
  IG="hw8-ig-$Z"
  if ! gcloud compute backend-services describe hw8-backend \
    --project="$PROJECT_ID" --region="$REGION" --format="value(backends[].group)" | grep -q "/instanceGroups/$IG"; then
    gcloud compute backend-services add-backend hw8-backend \
      --project="$PROJECT_ID" --region="$REGION" \
      --instance-group="$IG" --instance-group-zone="$REGION-$Z"
  fi
done

if ! exists gcloud compute addresses describe hw8-lb-ip --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute addresses create hw8-lb-ip \
    --project="$PROJECT_ID" --region="$REGION" --network-tier=PREMIUM
fi
if ! exists gcloud compute forwarding-rules describe hw8-forward --project="$PROJECT_ID" --region="$REGION"; then
  gcloud compute forwarding-rules create hw8-forward \
    --project="$PROJECT_ID" --region="$REGION" \
    --load-balancing-scheme=EXTERNAL --network-tier=PREMIUM \
    --ip-protocol=TCP --ports="$PORT" --address=hw8-lb-ip \
    --backend-service=hw8-backend
fi

LB_IP=$(gcloud compute addresses describe hw8-lb-ip \
  --project="$PROJECT_ID" --region="$REGION" --format="value(address)")
echo
echo "Load balancer: http://$LB_IP:$PORT/health"
echo "Check health:"
echo "gcloud compute backend-services get-health hw8-backend --project=$PROJECT_ID --region=$REGION"
echo "Test: curl -i --max-time 10 http://$LB_IP:$PORT/health"
echo "When finished: bash scripts/cleanup.sh --yes"

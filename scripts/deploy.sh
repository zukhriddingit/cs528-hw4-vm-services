#!/usr/bin/env bash
set -euo pipefail

# One command provisions both application VMs (plus a client VM).
# Prerequisites: gcloud authenticated, IAM permission to create SAs, VMs, bindings.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PROJECT_ID=${PROJECT_ID:-quick-formula-457319-q3}
BUCKET_NAME=${BUCKET_NAME:-cloud_hw2_bucket}
ZONE=${ZONE:-us-central1-a}
REGION=${ZONE%-*}
TOPIC_ID=${TOPIC_ID:-hw3-forbidden-requests}
SUBSCRIPTION_ID=${SUBSCRIPTION_ID:-hw4-forbidden-sub}

SERVER_VM=hw4-server
REPORTER_VM=hw4-reporter
CLIENT_VM=hw4-client
IP_NAME=hw4-server-ip
WEB_SA="hw4-web-sa@$PROJECT_ID.iam.gserviceaccount.com"
REPORTER_SA="hw4-reporter-sa@$PROJECT_ID.iam.gserviceaccount.com"
CLIENT_SA="hw4-client-sa@$PROJECT_ID.iam.gserviceaccount.com"

printf 'Project: %s | Zone: %s | Bucket: %s\n' "$PROJECT_ID" "$ZONE" "$BUCKET_NAME"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable compute.googleapis.com iam.googleapis.com logging.googleapis.com pubsub.googleapis.com storage.googleapis.com --project="$PROJECT_ID"
gcloud storage ls "gs://$BUCKET_NAME/pages/0.html" >/dev/null

ensure_sa() {
  local name="$1"
  local sa="$name@$PROJECT_ID.iam.gserviceaccount.com"
  if ! gcloud iam service-accounts describe "$sa" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$name" --display-name="CS528 HW4 $name" --project="$PROJECT_ID"
  fi
}
ensure_sa hw4-web-sa
ensure_sa hw4-reporter-sa
ensure_sa hw4-client-sa

# Least-privilege grants: web app reads HW2 objects, writes structured logs,
# and publishes forbidden events; reporter ONLY subscribes; client has no roles.
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
    --member="serviceAccount:$WEB_SA" --role=roles/storage.objectViewer >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$WEB_SA" --role=roles/logging.logWriter --quiet >/dev/null

if ! gcloud pubsub topics describe "$TOPIC_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub topics create "$TOPIC_ID" --project="$PROJECT_ID"
fi
if ! gcloud pubsub subscriptions describe "$SUBSCRIPTION_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$SUBSCRIPTION_ID" \
        --topic="$TOPIC_ID" --project="$PROJECT_ID"
fi
gcloud pubsub topics add-iam-policy-binding "$TOPIC_ID" \
    --member="serviceAccount:$WEB_SA" --role=roles/pubsub.publisher \
    --project="$PROJECT_ID" >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION_ID" \
    --member="serviceAccount:$REPORTER_SA" --role=roles/pubsub.subscriber \
    --project="$PROJECT_ID" >/dev/null

if ! gcloud compute addresses describe "$IP_NAME" --region="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute addresses create "$IP_NAME" --region="$REGION" --project="$PROJECT_ID"
fi
STATIC_IP=$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" \
    --project="$PROJECT_ID" --format='value(address)')

if ! gcloud compute firewall-rules describe hw4-allow-web --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute firewall-rules create hw4-allow-web \
        --project="$PROJECT_ID" --network=default --allow=tcp:8080 \
        --target-tags=hw4-web --source-ranges=0.0.0.0/0
fi

if ! gcloud compute instances describe "$SERVER_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$SERVER_VM" \
        --project="$PROJECT_ID" --zone="$ZONE" --machine-type=e2-micro \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=10GB --boot-disk-type=pd-standard \
        --address="$STATIC_IP" --tags=hw4-web \
        --service-account="$WEB_SA" --scopes=cloud-platform \
        --metadata="project-id=$PROJECT_ID,bucket-name=$BUCKET_NAME,topic-id=$TOPIC_ID" \
        --metadata-from-file="startup-script=$ROOT/scripts/startup-server.sh,server-code=$ROOT/service1/app.py,server-requirements=$ROOT/service1/requirements.txt,metrics-code=$ROOT/metrics/collect_metrics.py"
else
    echo "$SERVER_VM already exists; not recreating."
fi

if ! gcloud compute instances describe "$REPORTER_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$REPORTER_VM" \
        --project="$PROJECT_ID" --zone="$ZONE" --machine-type=e2-micro \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=10GB --boot-disk-type=pd-standard \
        --service-account="$REPORTER_SA" --scopes=cloud-platform \
        --metadata="project-id=$PROJECT_ID,subscription-id=$SUBSCRIPTION_ID" \
        --metadata-from-file="startup-script=$ROOT/scripts/startup-reporter.sh,reporter-code=$ROOT/service2/main.py,reporter-requirements=$ROOT/service2/requirements.txt"
else
    echo "$REPORTER_VM already exists; not recreating."
fi

if ! gcloud compute instances describe "$CLIENT_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$CLIENT_VM" \
        --project="$PROJECT_ID" --zone="$ZONE" --machine-type=e2-small \
        --image-family=debian-12 --image-project=debian-cloud \
        --boot-disk-size=10GB --boot-disk-type=pd-standard \
        --service-account="$CLIENT_SA" --scopes=cloud-platform
else
    echo "$CLIENT_VM already exists; not recreating."
fi

printf '\nCreated/checklisted VMs. Server public URL: http://%s:8080/0.html\n' "$STATIC_IP"
printf 'Startup status (server): gcloud compute ssh %s --zone=%s --command="sudo systemctl status hw4-server --no-pager"\n' "$SERVER_VM" "$ZONE"
printf 'Startup status (reporter): gcloud compute ssh %s --zone=%s --command="sudo systemctl status hw4-reporter --no-pager"\n' "$REPORTER_VM" "$ZONE"

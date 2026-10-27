#!/usr/bin/env bash
# Provision dedicated VM for two concurrent *original* course clients.
# Run from anywhere: bash hw5/launch_vm_load.sh 50000
set -Eeuo pipefail
HW5_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID='quick-formula-457319-q3'
ZONE='us-central1-a'
REGION='us-central1'
CLIENT_VM='hw5-client'
WEB_VM='hw5-server'
SQL_INSTANCE='hw5-sql'
SCHEDULER_JOB='hw5-hourly-sql-stop'
BUCKET='cloud_hw2_bucket'
CLIENT_SA="hw5-client-sa@${PROJECT_ID}.iam.gserviceaccount.com"
REQUESTS_PER_CLIENT="${1:-50000}"
[[ "$REQUESTS_PER_CLIENT" =~ ^[1-9][0-9]*$ ]] || { echo 'Expected positive request count'; exit 2; }
COURSE_BINARY="${HTTP_CLIENT_BIN:-$HOME/Downloads/http-client}"
[[ -f "$COURSE_BINARY" ]] || { echo "Course client not found: $COURSE_BINARY"; exit 1; }
for file in extract_course_client.py startup-client.sh cloud_vm_ops.py run_vm_load.py request_statistics.py setup_schema.py; do
    [[ -s "$HW5_DIR/$file" ]] || { echo "Missing $HW5_DIR/$file"; exit 1; }
done
GITIGNORE="$(dirname "$HW5_DIR")/.gitignore"
if [[ -f "$GITIGNORE" ]] && ! grep -qxF "hw5/.course_client_312.b64" "$GITIGNORE"; then
  printf "\nhw5/.course_client_312.b64\n" >> "$GITIGNORE"
fi
python3 "$HW5_DIR/extract_course_client.py" "$COURSE_BINARY" --output "$HW5_DIR/.course_client_312.b64"
PYTHON_BIN="$(command -v python3)"
"$PYTHON_BIN" -m py_compile "$HW5_DIR/cloud_vm_ops.py" "$HW5_DIR/run_vm_load.py"
bash -n "$HW5_DIR/startup-client.sh"

# Confirm API is reachable *before* provisioning. Never treat DNS as missing instance.
gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(state)' >/dev/null
gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" --project="$PROJECT_ID" --format='value(state)' >/dev/null
SERVER_IP="$(gcloud compute instances describe "$WEB_VM" --project="$PROJECT_ID" \
  --zone="$ZONE" --format='value(networkInterfaces[0].networkIP)')"
SERVER_STATE="$(gcloud compute instances describe "$WEB_VM" --project="$PROJECT_ID" --zone="$ZONE" --format='value(status)')"
[[ -n "$SERVER_IP" && "$SERVER_STATE" == 'RUNNING' ]] || { echo 'The HW5 server VM must be RUNNING'; exit 1; }
if gcloud compute instances describe "$CLIENT_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "$CLIENT_VM already exists. Not risking another concurrent run. Check existing VM status/logs." >&2
    exit 1
fi
# If a Mac-disconnected run left the scheduler paused, restore it before creating the VM.
STATE="$(gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" \
  --project="$PROJECT_ID" --format='value(state)')"
if [[ "$STATE" == 'PAUSED' ]]; then
    gcloud scheduler jobs resume "$SCHEDULER_JOB" --project="$PROJECT_ID" --location="$REGION"
fi

gcloud services enable compute.googleapis.com iam.googleapis.com sqladmin.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com storage.googleapis.com --project="$PROJECT_ID"
if ! gcloud iam service-accounts describe "$CLIENT_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create hw5-client-sa --project="$PROJECT_ID" \
      --display-name='CS528 HW5 dedicated client VM with autonomous cleanup'
fi
# Dedicated ephemeral VM SA; privileges are required to stop SQL even if Mac disconnects.
for role in roles/cloudsql.admin roles/cloudscheduler.admin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$CLIENT_SA" \
    --role="$role" --quiet >/dev/null
done
gcloud secrets add-iam-policy-binding hw5-db-password --project="$PROJECT_ID" \
  --member="serviceAccount:$CLIENT_SA" --role=roles/secretmanager.secretAccessor --quiet >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$CLIENT_SA" --role=roles/storage.objectCreator --quiet >/dev/null

CONFIG="$(mktemp)"
trap 'rm -f "$CONFIG"' EXIT
printf '{"requests_per_client":%s,"seed":5285,"server_internal_ip":"%s"}\n' \
  "$REQUESTS_PER_CLIENT" "$SERVER_IP" > "$CONFIG"
printf 'Launching %s with two %s-request course clients. Server private IP: %s\n' \
  "$CLIENT_VM" "$REQUESTS_PER_CLIENT" "$SERVER_IP"

gcloud compute instances create "$CLIENT_VM" --project="$PROJECT_ID" --zone="$ZONE" \
  --machine-type=e2-standard-2 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=20GB --boot-disk-type=pd-standard \
  --service-account="$CLIENT_SA" --scopes=cloud-platform \
  --metadata="project-id=$PROJECT_ID" \
  --metadata-from-file="startup-script=$HW5_DIR/startup-client.sh,course-client-b64=$HW5_DIR/.course_client_312.b64,cloud-ops-py=$HW5_DIR/cloud_vm_ops.py,runner-py=$HW5_DIR/run_vm_load.py,request-statistics-py=$HW5_DIR/request_statistics.py,setup-schema-py=$HW5_DIR/setup_schema.py,run-config=$CONFIG" \
  --quiet

echo
printf '%s created. The VM is installing Docker/Python; then it starts TWO original course clients independently of your Mac.\n' "$CLIENT_VM"
printf '\nMonitor after VM setup:\n'
printf 'gcloud compute ssh %s --project=%s --zone=%s --command="sudo tail -n 30 /var/log/hw5-load.log"\n' \
  "$CLIENT_VM" "$PROJECT_ID" "$ZONE"
printf '\nCheck VM startup errors:\n'
printf 'gcloud compute instances get-serial-port-output %s --project=%s --zone=%s --port=1 | tail -n 45\n' \
  "$CLIENT_VM" "$PROJECT_ID" "$ZONE"
printf '\nVM uploads evidence to gs://%s/hw5_evidence/ and stops itself when finished.\n' "$BUCKET"

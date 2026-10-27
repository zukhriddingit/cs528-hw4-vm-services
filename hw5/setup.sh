#!/usr/bin/env bash
# CS 528 HW5: grader-compatible deployment. Run from any working directory.
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

for program in gcloud python3 openssl curl; do
    command -v "$program" >/dev/null || { echo "Missing $program" >&2; exit 1; }
done
for file in "$HW5_DIR"/{setup_schema.py,check_schema.py,schema.sql,startup.sh,startup-reporter.sh,service1/app.py,service1/requirements.txt,service2/main.py,service2/requirements.txt,sql_monitor/main.py,sql_monitor/requirements.txt}; do
    [[ -s "$file" ]] || { echo "Missing file $file" >&2; exit 1; }
done

# If setup fails after starting the database, stop its compute charges.
DEPLOY_OK=0
finish_setup() {
    local rc=$?
    trap - EXIT
    if [[ "$DEPLOY_OK" != 1 ]]; then
        echo 'Setup incomplete. Attempting to stop Cloud SQL for safety.' >&2
        if ! sql_stop; then
            echo 'WARNING: Could not stop Cloud SQL; run cleanup.sh or stop it manually.' >&2
        fi
    fi
    # Never leave the hourly safety schedule paused after a failed setup.
    gcloud scheduler jobs resume "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --quiet >/dev/null 2>&1 || true
    exit "$rc"
}
trap finish_setup EXIT

echo "Deploying HW5 into hardcoded GCP project: $PROJECT_ID"
gcloud services enable --project="$PROJECT_ID" \
  compute.googleapis.com iam.googleapis.com sqladmin.googleapis.com \
  storage.googleapis.com logging.googleapis.com pubsub.googleapis.com \
  secretmanager.googleapis.com cloudfunctions.googleapis.com \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com eventarc.googleapis.com

# Prepare local Python tooling BEFORE turning on billable Cloud SQL compute.
PYTHON="$HW5_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then python3 -m venv "$HW5_DIR/.venv"; fi
"$PYTHON" -m pip install --disable-pip-version-check \
  'cloud-sql-python-connector[pg8000]>=1.18,<3'

for sa in hw5-web-sa hw5-reporter-sa hw5-guard-sa hw5-scheduler-sa; do
    ensure_sa "$sa"
done

# Reuse the user's .db_password when present, or reuse an existing secret.
# Fresh grader checkouts have neither: generate securely without prompts.
PASSWORD_FILE="$HW5_DIR/.db_password"
if [[ ! -s "$PASSWORD_FILE" ]]; then
    umask 077
    if gcloud secrets describe "$SECRET_ID" --project="$PROJECT_ID" >/dev/null 2>&1 && \
       gcloud secrets versions access latest --secret="$SECRET_ID" \
         --project="$PROJECT_ID" --out-file="$PASSWORD_FILE" >/dev/null 2>&1 && \
       [[ -s "$PASSWORD_FILE" ]]; then
        echo 'Reused existing database password from Secret Manager.'
    else
        openssl rand -hex 24 > "$PASSWORD_FILE"
        echo 'Generated a new private database password.'
    fi
fi
chmod 600 "$PASSWORD_FILE"

if ! gcloud secrets describe "$SECRET_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$SECRET_ID" --project="$PROJECT_ID" \
      --replication-policy=automatic --data-file="$PASSWORD_FILE"
else
    # Keep the VM's latest secret in sync with the local password, without
    # creating an unnecessary version when its content already matches.
    TEMP_DIR="$(mktemp -d)"
    TEMP_SECRET="$TEMP_DIR/secret"
    if ! gcloud secrets versions access latest --secret="$SECRET_ID" \
        --project="$PROJECT_ID" --out-file="$TEMP_SECRET" >/dev/null 2>&1 || \
        ! cmp -s "$TEMP_SECRET" "$PASSWORD_FILE"; then
        gcloud secrets versions add "$SECRET_ID" --project="$PROJECT_ID" \
          --data-file="$PASSWORD_FILE" >/dev/null
    fi
    rm -rf "$TEMP_DIR"
fi

gcloud secrets add-iam-policy-binding "$SECRET_ID" --project="$PROJECT_ID" \
  --member="serviceAccount:$WEB_SA" \
  --role=roles/secretmanager.secretAccessor --quiet >/dev/null
for role in roles/cloudsql.client roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:$WEB_SA" --role="$role" --quiet >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
  --member="serviceAccount:$WEB_SA" \
  --role=roles/storage.objectViewer --quiet >/dev/null
gcloud storage ls "gs://$BUCKET_NAME/pages/0.html" >/dev/null

if ! gcloud pubsub topics describe "$TOPIC_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub topics create "$TOPIC_ID" --project="$PROJECT_ID"
fi
if ! gcloud pubsub subscriptions describe "$SUBSCRIPTION_ID" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud pubsub subscriptions create "$SUBSCRIPTION_ID" --project="$PROJECT_ID" \
      --topic="$TOPIC_ID"
fi
gcloud pubsub topics add-iam-policy-binding "$TOPIC_ID" --project="$PROJECT_ID" \
  --member="serviceAccount:$WEB_SA" \
  --role=roles/pubsub.publisher --quiet >/dev/null
gcloud pubsub subscriptions add-iam-policy-binding "$SUBSCRIPTION_ID" --project="$PROJECT_ID" \
  --member="serviceAccount:$REPORTER_SA" \
  --role=roles/pubsub.subscriber --quiet >/dev/null

# The guard runs hourly unless explicitly paused during long load tests.
# SQL Editor (not Admin) permits reading/stopping the instance but not user changes.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$GUARD_SA" \
  --role=roles/cloudsql.editor --quiet >/dev/null
if ! gcloud functions describe "$GUARD_FUNCTION" --gen2 --region="$REGION" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo 'Deploying private hourly Cloud SQL stop function...'
    gcloud functions deploy "$GUARD_FUNCTION" --gen2 --project="$PROJECT_ID" \
      --region="$REGION" --runtime=python312 --source="$HW5_DIR/sql_monitor" \
      --entry-point=stop_cloud_sql --trigger-http --no-allow-unauthenticated \
      --service-account="$GUARD_SA" --timeout=120s --memory=256MiB --quiet
fi
GUARD_URL="$(gcloud functions describe "$GUARD_FUNCTION" --gen2 --region="$REGION" \
  --project="$PROJECT_ID" --format='value(serviceConfig.uri)')"
[[ "$GUARD_URL" == https://* ]] || { echo 'No usable Cloud Function URL returned' >&2; exit 1; }
gcloud run services add-iam-policy-binding "$GUARD_FUNCTION" --project="$PROJECT_ID" \
  --region="$REGION" --member="serviceAccount:$SCHEDULER_SA" \
  --role=roles/run.invoker --quiet >/dev/null
if ! gcloud scheduler jobs describe "$SCHEDULER_JOB" --location="$REGION" \
  --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud scheduler jobs create http "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --schedule='0 * * * *' --time-zone='Etc/UTC' \
      --uri="$GUARD_URL" --http-method=POST \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$GUARD_URL" --max-retry-attempts=2 --quiet
else
    gcloud scheduler jobs update http "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --schedule='0 * * * *' --time-zone='Etc/UTC' \
      --uri="$GUARD_URL" --http-method=POST \
      --oidc-service-account-email="$SCHEDULER_SA" \
      --oidc-token-audience="$GUARD_URL" --quiet
    gcloud scheduler jobs resume "$SCHEDULER_JOB" --project="$PROJECT_ID" \
      --location="$REGION" --quiet 2>/dev/null || true
fi

# Prevent a scheduled stop colliding with installation. The EXIT trap resumes it.
gcloud scheduler jobs pause "$SCHEDULER_JOB" --project="$PROJECT_ID" \
  --location="$REGION" --quiet

# Reuse existing SQL rather than recreating it. NEVER cannot be used on create;
# start first and ensure start/stop operations fully complete (avoids HTTP 409).
wait_sql_operations
if ! sql_exists; then
    sql_operation 'Creating Cloud SQL PostgreSQL 16 instance' \
      gcloud sql instances create "$SQL_INSTANCE" --project="$PROJECT_ID" \
      --database-version=POSTGRES_16 --edition=ENTERPRISE \
      --tier=db-f1-micro --region="$REGION" --availability-type=ZONAL \
      --storage-size=10GB --storage-type=SSD --no-backup \
      --no-storage-auto-increase --assign-ip --quiet
fi
POLICY="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
  --format='value(settings.activationPolicy)')"
if [[ "$POLICY" != 'ALWAYS' ]]; then
    sql_operation "Starting existing Cloud SQL $SQL_INSTANCE" \
      gcloud sql instances patch "$SQL_INSTANCE" --project="$PROJECT_ID" \
      --activation-policy=ALWAYS --quiet
fi
SQL_STATE="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
  --format='value(state)')"
[[ "$SQL_STATE" == 'RUNNABLE' ]] || { echo "Cloud SQL not ready: $SQL_STATE" >&2; exit 1; }

# Ensure user exists; reset its password to match locally stored and Secret Manager.
if ! gcloud sql users list --instance="$SQL_INSTANCE" --project="$PROJECT_ID" \
    --format='value(name)' | grep -qx "$DB_USER"; then
    sql_operation 'Creating HW5 PostgreSQL user' \
      gcloud sql users create "$DB_USER" --instance="$SQL_INSTANCE" \
      --project="$PROJECT_ID" --password="$(cat "$PASSWORD_FILE")"
else
    sql_operation 'Synchronizing HW5 PostgreSQL password' \
      gcloud sql users set-password "$DB_USER" --instance="$SQL_INSTANCE" \
      --project="$PROJECT_ID" --password="$(cat "$PASSWORD_FILE")"
fi

# Schema setup is idempotent. Skip table creation on the normal existing path,
# repairing an incomplete schema when the check fails.
DB_PASS="$(cat "$PASSWORD_FILE")" \
  INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}" \
  "$PYTHON" "$HW5_DIR/check_schema.py" >/dev/null && SCHEMA_READY=1 || SCHEMA_READY=0
if [[ "$SCHEMA_READY" == 1 ]]; then
    echo 'Database and both tables already exist; skipping schema creation.'
else
    DB_PASS="$(cat "$PASSWORD_FILE")" \
      INSTANCE_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}" \
      "$PYTHON" "$HW5_DIR/setup_schema.py"
fi

if ! gcloud compute addresses describe "$IP_NAME" --region="$REGION" \
    --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute addresses create "$IP_NAME" --region="$REGION" --project="$PROJECT_ID"
fi
SERVER_IP="$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" \
  --project="$PROJECT_ID" --format='value(address)')"
[[ -n "$SERVER_IP" ]] || { echo 'Could not resolve reserved IP' >&2; exit 1; }
if ! gcloud compute firewall-rules describe "$FIREWALL_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "$FIREWALL_NAME" --project="$PROJECT_ID" \
      --network=default --allow=tcp:8080 --target-tags=hw5-web \
      --source-ranges=0.0.0.0/0
fi

# startup.sh runs on the VM via metadata. NO ssh --command is used for provisioning.
if ! gcloud compute instances describe "$SERVER_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$SERVER_VM" --project="$PROJECT_ID" \
      --zone="$ZONE" --machine-type=e2-standard-2 \
      --image-family=debian-12 --image-project=debian-cloud \
      --boot-disk-size=10GB --boot-disk-type=pd-standard \
      --address="$SERVER_IP" --tags=hw5-web \
      --service-account="$WEB_SA" --scopes=cloud-platform \
      --metadata="project-id=$PROJECT_ID,bucket-name=$BUCKET_NAME,topic-id=$TOPIC_ID,db-secret-id=$SECRET_ID,instance-connection-name=$PROJECT_ID:$REGION:$SQL_INSTANCE" \
      --metadata-from-file="startup-script=$HW5_DIR/startup.sh,server-code=$HW5_DIR/service1/app.py,server-requirements=$HW5_DIR/service1/requirements.txt"
else
    gcloud compute instances add-metadata "$SERVER_VM" --project="$PROJECT_ID" --zone="$ZONE" \
      --metadata="project-id=$PROJECT_ID,bucket-name=$BUCKET_NAME,topic-id=$TOPIC_ID,db-secret-id=$SECRET_ID,instance-connection-name=$PROJECT_ID:$REGION:$SQL_INSTANCE" \
      --metadata-from-file="startup-script=$HW5_DIR/startup.sh,server-code=$HW5_DIR/service1/app.py,server-requirements=$HW5_DIR/service1/requirements.txt"
    STATUS="$(gcloud compute instances describe "$SERVER_VM" --project="$PROJECT_ID" --zone="$ZONE" --format='value(status)')"
    if [[ "$STATUS" == 'TERMINATED' ]]; then
        gcloud compute instances start "$SERVER_VM" --project="$PROJECT_ID" --zone="$ZONE"
    elif [[ "$STATUS" == 'RUNNING' ]]; then
        gcloud compute instances reset "$SERVER_VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet
    else
        echo "Unexpected $SERVER_VM state: $STATUS" >&2; exit 1
    fi
fi

if ! gcloud compute instances describe "$REPORTER_VM" --zone="$ZONE" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud compute instances create "$REPORTER_VM" --project="$PROJECT_ID" \
      --zone="$ZONE" --machine-type=e2-micro \
      --image-family=debian-12 --image-project=debian-cloud \
      --boot-disk-size=10GB --boot-disk-type=pd-standard \
      --service-account="$REPORTER_SA" --scopes=cloud-platform \
      --metadata="project-id=$PROJECT_ID,subscription-id=$SUBSCRIPTION_ID" \
      --metadata-from-file="startup-script=$HW5_DIR/startup-reporter.sh,reporter-code=$HW5_DIR/service2/main.py,reporter-requirements=$HW5_DIR/service2/requirements.txt"
else
    gcloud compute instances add-metadata "$REPORTER_VM" --project="$PROJECT_ID" --zone="$ZONE" \
      --metadata="project-id=$PROJECT_ID,subscription-id=$SUBSCRIPTION_ID" \
      --metadata-from-file="startup-script=$HW5_DIR/startup-reporter.sh,reporter-code=$HW5_DIR/service2/main.py,reporter-requirements=$HW5_DIR/service2/requirements.txt"
    STATUS="$(gcloud compute instances describe "$REPORTER_VM" --project="$PROJECT_ID" --zone="$ZONE" --format='value(status)')"
    if [[ "$STATUS" == 'TERMINATED' ]]; then
        gcloud compute instances start "$REPORTER_VM" --project="$PROJECT_ID" --zone="$ZONE"
    elif [[ "$STATUS" == 'RUNNING' ]]; then
        gcloud compute instances reset "$REPORTER_VM" --project="$PROJECT_ID" --zone="$ZONE" --quiet
    else
        echo "Unexpected $REPORTER_VM state: $STATUS" >&2; exit 1
    fi
fi

gcloud scheduler jobs resume "$SCHEDULER_JOB" --project="$PROJECT_ID" \
  --location="$REGION" --quiet
DEPLOY_OK=1
printf '\nHW5 infrastructure provisioned.\nServer IP: %s\nURL: http://%s:8080/0.html\n' "$SERVER_IP" "$SERVER_IP"
echo 'The first VM boot installs packages asynchronously; check its startup logs before testing.'
echo "The hourly SQL-stop scheduler is enabled. Pause it only for active long stress runs."
echo 'When finished: bash hw5/cleanup.sh'

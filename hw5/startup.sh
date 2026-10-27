#!/bin/bash
# CS 528 HW5 web-VM configuration. Passed with --metadata-from-file.
# Runs as root. On later boots skips OS/Python package installation.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

METADATA_URL='http://metadata.google.internal/computeMetadata/v1'
meta() {
    curl -fsS --retry 12 --retry-delay 2 -H 'Metadata-Flavor: Google' \
      "$METADATA_URL/instance/attributes/$1"
}

FIRST_BOOT=0
LOCK_FILE='/var/log/hw5_server_startup_already_done'
if [[ -f "$LOCK_FILE" ]]; then
    echo 'HW5 web setup already ran. Skipping package installation.'
else
    FIRST_BOOT=1
    apt-get update -y
    apt-get install -y --no-install-recommends \
      ca-certificates curl python3-venv python3-pip
    mkdir -p /opt/hw5/server
    python3 -m venv /opt/hw5/server/.venv
fi

mkdir -p /opt/hw5/server /etc/hw5
meta server-code > /opt/hw5/server/app.py
meta server-requirements > /opt/hw5/server/requirements.txt
PROJECT_ID="$(meta project-id)"
BUCKET_NAME="$(meta bucket-name)"
TOPIC_ID="$(meta topic-id)"
SECRET_ID="$(meta db-secret-id)"
INSTANCE_CONNECTION_NAME="$(meta instance-connection-name)"
if [[ "$FIRST_BOOT" == 1 ]]; then
    /opt/hw5/server/.venv/bin/pip install --no-cache-dir \
      -r /opt/hw5/server/requirements.txt
fi

# Fetch DB password from Secret Manager with this VM's service identity.
# Unlike putting DB_PASS in metadata, the secret is not broadly retrievable.
TOKEN="$(curl -fsS --retry 8 --retry-delay 2 -H 'Metadata-Flavor: Google' \
  "$METADATA_URL/instance/service-accounts/default/token" | \
  python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"
umask 077
curl -fsS --retry 8 --retry-delay 2 \
  -H "Authorization: Bearer $TOKEN" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SECRET_ID}/versions/latest:access" | \
  python3 -c 'import json,base64,sys; p=json.load(sys.stdin); open("/etc/hw5/db_password","wb").write(base64.b64decode(p["payload"]["data"]))'
unset TOKEN
chown root:www-data /etc/hw5/db_password
chmod 640 /etc/hw5/db_password

cat > /etc/hw5/web.env <<END_ENV
PROJECT_ID=$PROJECT_ID
BUCKET_NAME=$BUCKET_NAME
TOPIC_ID=$TOPIC_ID
INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME
DB_NAME=hw5_requests
DB_USER=hw5_user
DB_PASS_FILE=/etc/hw5/db_password
HW5_METRICS_FILE=/tmp/hw5_timings.csv
END_ENV
chmod 644 /etc/hw5/web.env

cat > /etc/systemd/system/hw5-web.service <<'END_UNIT'
[Unit]
Description=CS528 HW5 Flask GCS / Cloud SQL server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/hw5/server
EnvironmentFile=/etc/hw5/web.env
ExecStart=/opt/hw5/server/.venv/bin/gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 8 --timeout 120 app:app
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
END_UNIT

systemctl daemon-reload
systemctl enable hw5-web
systemctl restart hw5-web
touch "$LOCK_FILE"
echo 'HW5 web VM startup configuration succeeded.'

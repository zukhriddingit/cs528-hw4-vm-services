#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

METADATA="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
get_meta() { curl -fsS --retry 10 --retry-delay 2 -H 'Metadata-Flavor: Google' "$METADATA/$1"; }

mkdir -p /opt/hw4/server
get_meta server-code > /opt/hw4/server/app.py
get_meta server-requirements > /opt/hw4/server/requirements.txt
get_meta metrics-code > /opt/hw4/server/collect_metrics.py
PROJECT_ID=$(get_meta project-id)
BUCKET_NAME=$(get_meta bucket-name)
TOPIC_ID=$(get_meta topic-id)

apt-get update -y
apt-get install -y --no-install-recommends python3-venv python3-pip ca-certificates
python3 -m venv /opt/hw4/server/.venv
/opt/hw4/server/.venv/bin/pip install --no-cache-dir -r /opt/hw4/server/requirements.txt

cat > /etc/hw4-server.env <<EOF
PROJECT_ID=$PROJECT_ID
BUCKET_NAME=$BUCKET_NAME
TOPIC_ID=$TOPIC_ID
EOF

cat > /etc/systemd/system/hw4-server.service <<'EOF'
[Unit]
Description=CS528 HW4 Python GCS Web Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/hw4/server
EnvironmentFile=/etc/hw4-server.env
ExecStart=/opt/hw4/server/.venv/bin/gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 60 app:app
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hw4-server
systemctl restart hw4-server

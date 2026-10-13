#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

METADATA="http://metadata.google.internal/computeMetadata/v1/instance/attributes"
get_meta() { curl -fsS --retry 10 --retry-delay 2 -H 'Metadata-Flavor: Google' "$METADATA/$1"; }

mkdir -p /opt/hw4/reporter
get_meta reporter-code > /opt/hw4/reporter/main.py
get_meta reporter-requirements > /opt/hw4/reporter/requirements.txt
PROJECT_ID=$(get_meta project-id)
SUBSCRIPTION_ID=$(get_meta subscription-id)

apt-get update -y
apt-get install -y --no-install-recommends python3-venv python3-pip ca-certificates
python3 -m venv /opt/hw4/reporter/.venv
/opt/hw4/reporter/.venv/bin/pip install --no-cache-dir -r /opt/hw4/reporter/requirements.txt

cat > /etc/hw4-reporter.env <<EOF
PROJECT_ID=$PROJECT_ID
SUBSCRIPTION_ID=$SUBSCRIPTION_ID
EOF

cat > /etc/systemd/system/hw4-reporter.service <<'EOF'
[Unit]
Description=CS528 HW4 Forbidden Country Pub/Sub Reporter
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/hw4/reporter
EnvironmentFile=/etc/hw4-reporter.env
ExecStart=/opt/hw4/reporter/.venv/bin/python -u main.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hw4-reporter
systemctl restart hw4-reporter

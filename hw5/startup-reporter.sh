#!/bin/bash
# Unmodified HW4 Pub/Sub reporter, deployed via VM startup metadata.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
METADATA_URL='http://metadata.google.internal/computeMetadata/v1/instance/attributes'
meta() { curl -fsS --retry 12 --retry-delay 2 -H 'Metadata-Flavor: Google' "$METADATA_URL/$1"; }
LOCK_FILE='/var/log/hw5_reporter_startup_already_done'
if [[ ! -f "$LOCK_FILE" ]]; then
    apt-get update -y
    apt-get install -y --no-install-recommends ca-certificates curl python3-venv python3-pip
    mkdir -p /opt/hw5/reporter
    python3 -m venv /opt/hw5/reporter/.venv
    FIRST_BOOT=1
else
    echo 'HW5 reporter setup already ran. Skipping package installation.'
    FIRST_BOOT=0
fi
mkdir -p /opt/hw5/reporter /etc/hw5
meta reporter-code > /opt/hw5/reporter/main.py
meta reporter-requirements > /opt/hw5/reporter/requirements.txt
if [[ "$FIRST_BOOT" == 1 ]]; then
    /opt/hw5/reporter/.venv/bin/pip install --no-cache-dir -r /opt/hw5/reporter/requirements.txt
fi
PROJECT_ID="$(meta project-id)"
SUBSCRIPTION_ID="$(meta subscription-id)"
cat > /etc/hw5/reporter.env <<END_ENV
PROJECT_ID=$PROJECT_ID
SUBSCRIPTION_ID=$SUBSCRIPTION_ID
END_ENV
cat > /etc/systemd/system/hw5-reporter.service <<'END_UNIT'
[Unit]
Description=CS528 HW5 PubSub forbidden-country reporter
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/hw5/reporter
EnvironmentFile=/etc/hw5/reporter.env
ExecStart=/opt/hw5/reporter/.venv/bin/python -u main.py
Restart=always
RestartSec=4
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
END_UNIT
systemctl daemon-reload
systemctl enable hw5-reporter
systemctl restart hw5-reporter
touch "$LOCK_FILE"
echo 'HW5 reporter VM startup configuration succeeded.'

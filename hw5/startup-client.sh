#!/usr/bin/env bash
# CS528 HW5 dedicated Linux load-VM startup; all configuration occurs here.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
LOCK=/var/log/hw5_client_startup_already_done
BASE=/opt/hw5/client
META='http://metadata.google.internal/computeMetadata/v1/instance/attributes'
meta() { curl -fsS --retry 10 --retry-delay 2 -H 'Metadata-Flavor: Google' "$META/$1"; }
if [[ -f "$LOCK" ]]; then
  echo 'HW5 client configuration already installed; skipping.'
  exit 0
fi
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl docker.io python3 python3-venv python3-pip
systemctl enable --now docker
mkdir -p "$BASE/evidence"
chmod 700 "$BASE"
meta course-client-b64 | base64 -d > "$BASE/course-client.pyc"
meta cloud-ops-py > "$BASE/cloud_vm_ops.py"
meta runner-py > "$BASE/run_vm_load.py"
meta request-statistics-py > "$BASE/request_statistics.py"
meta setup-schema-py > "$BASE/setup_schema.py"
meta run-config > "$BASE/config.json"
python3 -m venv "$BASE/.venv"
"$BASE/.venv/bin/pip" install --no-cache-dir 'cloud-sql-python-connector[pg8000]'
chmod 600 "$BASE/course-client.pyc" "$BASE/config.json"
python3 -m py_compile "$BASE/cloud_vm_ops.py" "$BASE/run_vm_load.py" "$BASE/request_statistics.py" "$BASE/setup_schema.py"
# Prepull Python 3.12 and test the *unaltered* original client BEFORE SQL starts.
docker pull python:3.12-slim-bookworm
docker run --rm --mount "type=bind,src=$BASE/course-client.pyc,dst=/work/http-client.pyc,readonly" \
  python:3.12-slim-bookworm python /work/http-client.pyc --help >/dev/null
cat > /etc/systemd/system/hw5-load.service <<'UNIT'
[Unit]
Description=CS528 HW5 2x50000 original course clients, with Cloud SQL lifecycle
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/hw5/client
ExecStart=/opt/hw5/client/.venv/bin/python /opt/hw5/client/run_vm_load.py
StandardOutput=append:/var/log/hw5-load.log
StandardError=append:/var/log/hw5-load.log
TimeoutStartSec=infinity
Restart=no

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
touch "$LOCK"
systemctl start --no-block hw5-load.service
# Do NOT enable on subsequent boots; a restarted VM must not repeat 100k requests.
echo "HW5 client VM installation finished. Load starting independently."

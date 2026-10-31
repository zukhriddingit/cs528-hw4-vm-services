#!/usr/bin/env bash
# GCE Linux startup script: run HW6 training remotely, report status in GCS.
# Attached as VM metadata by step3_run_vm.sh (not executed on the Mac).
set -Eeuo pipefail
umask 077
mkdir -p /opt/hw6 /var/log
exec >> /var/log/hw6-bootstrap.log 2>&1
# The old script showed no training progress over the serial console.
# These sparse stage messages remain visible even while detailed logs go to disk.
echo 'HW6 BOOTSTRAP STARTED' > /dev/console 2>/dev/null || true

echo "HW6 startup initialized at $(date -u -Iseconds)"
meta() {
    curl --connect-timeout 10 --max-time 30 -fsS \
      -H 'Metadata-Flavor: Google' \
      "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"
}
export HW6_PROJECT="$(meta hw6-project)"
export HW6_BUCKET="$(meta hw6-bucket)"
export HW6_RUN_ID="$(meta hw6-run-id)"
export HW6_SQL_INSTANCE="$(meta hw6-sql-instance)"
export HW6_SECRET="$(meta hw6-secret)"
export HW6_PREFIX="hw6/runs/$HW6_RUN_ID"

# Self-contained Google Cloud Storage transfer helper using only Python stdlib
# and the VM's metadata-server access token. This works even if pip fails.
cat > /opt/hw6/bootstrap_gcs.py <<'PY'
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def token():
    req = urllib.request.Request(
        'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
        headers={'Metadata-Flavor': 'Google'},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)['access_token']


def transfer(verb, bucket, name, path):
    b = urllib.parse.quote(bucket, safe='')
    obj = urllib.parse.quote(name, safe='')
    if verb == 'download':
        url = f'https://storage.googleapis.com/storage/v1/b/{b}/o/{obj}?alt=media'
        data = None
        method = 'GET'
    else:
        url = f'https://storage.googleapis.com/upload/storage/v1/b/{b}/o?uploadType=media&name={obj}'
        data = Path(path).read_bytes()
        method = 'POST'
    for attempt in range(int(__import__('os').environ.get('HW6_GCS_MAX_ATTEMPTS', '12'))):
        try:
            req = urllib.request.Request(
                url, method=method, data=data,
                headers={'Authorization': f'Bearer {token()}',
                         'Content-Type': 'application/octet-stream'},
            )
            with urllib.request.urlopen(req, timeout=120) as response:
                result = response.read()
            if verb == 'download':
                Path(path).write_bytes(result)
            return
        except Exception as exc:
            print(f'GCS {verb} attempt {attempt + 1} failed: {type(exc).__name__}: {exc}',
                  file=sys.stderr, flush=True)
            if attempt + 1 == int(__import__('os').environ.get('HW6_GCS_MAX_ATTEMPTS', '12')):
                raise
            time.sleep(5 * (attempt + 1))


if __name__ == '__main__':
    transfer(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
PY

# Publish stage markers immediately; while a long command executes, sync logs
# to GCS periodically so a timeout still leaves useful diagnostics.
progress() {
    local stage="$1"
    echo "HW6 STAGE: $stage at $(date -u -Iseconds)"
    printf 'HW6 STAGE: %s\n' "$stage" > /dev/console 2>/dev/null || true
    HW6_STAGE="$stage" python3 - <<'PY_STATUS' > /opt/hw6/progress.json
import datetime, json, os
print(json.dumps({'stage': os.environ['HW6_STAGE'],
                  'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  'run_id': os.environ['HW6_RUN_ID']}))
PY_STATUS
    HW6_GCS_MAX_ATTEMPTS=2 python3 /opt/hw6/bootstrap_gcs.py upload \
        "$HW6_BUCKET" "$HW6_PREFIX/progress.json" /opt/hw6/progress.json || true
}

sync_logs() {
    while sleep 60; do
        for name in bootstrap pip models; do
            local path="/var/log/hw6-$name.log"
            [[ -f "$path" ]] || continue
            HW6_GCS_MAX_ATTEMPTS=1 python3 /opt/hw6/bootstrap_gcs.py upload \
                "$HW6_BUCKET" "$HW6_PREFIX/$name.log" "$path" || true
        done
    done
}
LOG_SYNC_PID=""

finalize() {
    local result=$?
    trap - EXIT INT TERM
    set +e
    if [[ -n "${LOG_SYNC_PID:-}" ]]; then kill "$LOG_SYNC_PID" 2>/dev/null || true; fi
    echo "HW6 startup exiting: result=$result time=$(date -u -Iseconds)"
    printf 'HW6 EXIT CODE: %s\n' "$result" > /dev/console 2>/dev/null || true
    python3 /opt/hw6/bootstrap_gcs.py upload "$HW6_BUCKET" \
      "$HW6_PREFIX/bootstrap.log" /var/log/hw6-bootstrap.log || true
    if [[ -f /var/log/hw6-pip.log ]]; then
        python3 /opt/hw6/bootstrap_gcs.py upload "$HW6_BUCKET" \
          "$HW6_PREFIX/pip.log" /var/log/hw6-pip.log || true
    fi
    if [[ -f /var/log/hw6-models.log ]]; then
        python3 /opt/hw6/bootstrap_gcs.py upload "$HW6_BUCKET" \
          "$HW6_PREFIX/models.log" /var/log/hw6-models.log || true
    fi
    export HW6_RESULT="$result"
    python3 - <<'PY' > /opt/hw6/status.json
import datetime, json, os
print(json.dumps({
    'success': os.environ['HW6_RESULT'] == '0',
    'exit_code': int(os.environ['HW6_RESULT']),
    'run_id': os.environ['HW6_RUN_ID'],
    'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
}))
PY
    if ! python3 /opt/hw6/bootstrap_gcs.py upload "$HW6_BUCKET" \
             "$HW6_PREFIX/status.json" /opt/hw6/status.json; then
        echo 'CRITICAL: failed to upload VM run status to Cloud Storage.' >&2
        result=1
    fi
    exit "$result"
}
trap finalize EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
sync_logs & LOG_SYNC_PID=$!
progress 'bootstrap-ready'

printf 'Downloading job bundle from gs://%s/%s/job.zip\n' "$HW6_BUCKET" "$HW6_PREFIX"
python3 /opt/hw6/bootstrap_gcs.py download "$HW6_BUCKET" \
    "$HW6_PREFIX/job.zip" /opt/hw6/job.zip
progress 'extracting-job-bundle'
python3 - <<'PY'
import zipfile
with zipfile.ZipFile('/opt/hw6/job.zip') as z:
    for name in z.namelist():
        if name not in {'step3_models.py', 'requirements_step3.txt'}:
            raise ValueError(f'Unexpected archive entry: {name}')
    z.extractall('/opt/hw6')
PY

progress 'installing-dependencies'
echo 'Installing Python 3 virtual environment and training packages...'
export DEBIAN_FRONTEND=noninteractive
timeout --signal=TERM --kill-after=30s 600s apt-get update -qq > /var/log/hw6-pip.log 2>&1
timeout --signal=TERM --kill-after=30s 600s apt-get install -o DPkg::Lock::Timeout=120 -y -qq python3-venv python3-pip >> /var/log/hw6-pip.log 2>&1
python3 -m venv /opt/hw6/.venv
timeout --signal=TERM --kill-after=30s 900s /opt/hw6/.venv/bin/python -m pip install --disable-pip-version-check --no-input \
    -r /opt/hw6/requirements_step3.txt >> /var/log/hw6-pip.log 2>&1

progress 'training-models'
echo 'Training models on Compute Engine VM...'
/opt/hw6/.venv/bin/python /opt/hw6/step3_models.py \
    --project "$HW6_PROJECT" \
    --bucket "$HW6_BUCKET" \
    --prefix "$HW6_PREFIX" \
    --sql-instance "$HW6_SQL_INSTANCE" \
    --secret "$HW6_SECRET" \
    --output-dir /opt/hw6/output 2>&1 | tee /var/log/hw6-models.log

progress 'results-uploaded'
echo 'Both model runs finished and artifacts uploaded.'

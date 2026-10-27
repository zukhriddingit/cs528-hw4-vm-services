#!/usr/bin/env bash
# CS528 HW5 step #4: one successful + two erroneous curl requests,
# showing BOTH Cloud SQL tables before and after EACH request.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$ROOT/_common.sh"
PY="$ROOT/.venv/bin/python"
EVIDENCE="$ROOT/evidence"
mkdir -p "$EVIDENCE"
OUT="$EVIDENCE/curl_$(date -u +%Y%m%dT%H%M%SZ).txt"
exec > >(tee "$OUT") 2>&1

[[ -x "$PY" ]] || { echo "Missing $PY; create hw5/.venv first"; exit 1; }
[[ -s "$ROOT/.db_password" ]] || { echo 'Missing local hw5/.db_password'; exit 1; }

state="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" --format='value(state)')"
if [[ "$state" != 'RUNNABLE' ]]; then
    echo "Cloud SQL isn't running (state=$state). Do not test until it is RUNNABLE."
    exit 1
fi

# Disable the automatic stop while actively testing; ALWAYS re-enable it on exit.
"$ROOT/pause_guard.sh"
resume_on_exit() {
    local code=$?
    trap - EXIT
    "$ROOT/resume_guard.sh" || echo 'WARNING: Could not re-enable hourly stop job. Resume it manually.'
    echo "Evidence captured at: $OUT"
    exit "$code"
}
trap resume_on_exit EXIT

IP="$(gcloud compute addresses describe "$IP_NAME" --region="$REGION" \
    --project="$PROJECT_ID" --format='value(address)')"
[[ -n "$IP" ]] || { echo 'Unable to get HW5 server IP'; exit 1; }
BASE="http://$IP:8080"

dump_tables() {
    "$PY" "$ROOT/inspect_tables.py" --limit 3
}

make_request() {
    local name="$1" expected="$2" path="$3" country="$4"
    local headers="$EVIDENCE/${name}_headers.txt" body="$EVIDENCE/${name}_body.txt"
    echo
    echo "========== BEFORE ${name}: /${path}, expected HTTP $expected =========="
    dump_tables
    echo
    echo "========== CURL ${name}: /${path} =========="
    local status
    status="$(curl --silent --show-error --max-time 120 \
      --dump-header "$headers" --output "$body" --write-out '%{http_code}' \
      --header "X-country: $country" \
      --header 'X-client-IP: 203.0.113.42' \
      --header 'X-gender: Male' \
      --header 'X-age: 26-35' \
      --header 'X-income: 40k-60k' \
      --header "X-time: $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      "$BASE/$path")"
    cat "$headers"
    echo "Returned HTTP $status (expected $expected)"
    if [[ "$status" != '200' ]]; then cat "$body"; echo; fi
    echo
    echo "========== AFTER ${name} =========="
    dump_tables
    if [[ "$status" != "$expected" ]]; then
        echo "ERROR: Expected $expected but got $status; inspect service logs."
        return 1
    fi
}

make_request 'success_200' '200' '0.html' 'USA'
make_request 'missing_404' '404' '999999.html' 'USA'
make_request 'banned_400' '400' '0.html' 'Iran'

echo
printf 'All three curl tests passed. Screenshots can be taken from this output.\n'

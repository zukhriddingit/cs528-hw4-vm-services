#!/usr/bin/env bash
# Run 2 identical-seed macOS course-client processes, then collect HW5 evidence.
# Examples: bash hw5/run_load.sh 100   or   caffeinate -i bash hw5/run_load.sh 50000
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

if [[ "${1:-}" == '--help' || "${1:-}" == '-h' ]]; then
    echo 'Usage: bash hw5/run_load.sh REQUESTS_PER_CLIENT  (e.g. 100 or 50000)'
    exit 0
fi
REQUESTS_PER_CLIENT="${1:-100}"
[[ "$REQUESTS_PER_CLIENT" =~ ^[1-9][0-9]*$ ]] || {
    echo 'Pass a positive number of requests per client.' >&2; exit 2;
}
SEED=5285
CLIENT_BIN="${HTTP_CLIENT_BIN:-$HOME/Downloads/http-client}"
PYTHON="$HW5_DIR/.venv/bin/python"
if [[ ! -x "$CLIENT_BIN" ]]; then
    echo "Cannot execute course client: $CLIENT_BIN" >&2; exit 1
fi
for file in "$PYTHON" "$HW5_DIR/request_statistics.py" "$HW5_DIR/filter_timings.py" "$HW5_DIR/analyze_timings.py" "$HW5_DIR/progress_monitor.py" "$HW5_DIR/.db_password"; do
    [[ -f "$file" ]] || { echo "Missing $file" >&2; exit 1; }
done

# Cheap local preflight BEFORE starting billable Cloud SQL resources.
"$PYTHON" "$HW5_DIR/request_statistics.py" --help >/dev/null
"$PYTHON" "$HW5_DIR/filter_timings.py" --help >/dev/null
"$PYTHON" "$HW5_DIR/progress_monitor.py" --help >/dev/null

# Fail before database startup if Google Cloud control-plane access is broken.
# An API outage during cleanup could otherwise leave the database running.
if ! gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
    --format='value(state,settings.activationPolicy)' >/dev/null; then
    echo 'Cloud SQL preflight failed. Check network/DNS before retrying.' >&2
    exit 1
fi
if ! gcloud scheduler jobs describe "$SCHEDULER_JOB" --project="$PROJECT_ID" \
    --location="$REGION" --format='value(state)' >/dev/null; then
    echo 'Cloud Scheduler preflight failed. Check network/DNS before retrying.' >&2
    exit 1
fi

# Resolve the reserved IP at runtime (never hardcode an address).
SERVER_IP="$(gcloud compute addresses describe "$IP_NAME" --project="$PROJECT_ID" \
  --region="$REGION" --format='value(address)')"
[[ -n "$SERVER_IP" ]] || { echo 'Missing reserved server IP' >&2; exit 1; }
[[ "$(gcloud compute instances describe "$SERVER_VM" --project="$PROJECT_ID" \
  --zone="$ZONE" --format='value(status)')" == 'RUNNING' ]] || {
    echo 'HW5 web VM is not running; deploy it before running the clients.' >&2; exit 1;
}

RUNSTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
RUN_DIR="$HW5_DIR/evidence/load_${RUNSTAMP}_${REQUESTS_PER_CLIENT}x2"
mkdir -p "$RUN_DIR"
GUARD_PAUSED=0
SQL_TOUCHED=0
P1=''
P2=''
MONITOR_PID=''

finish() {
    local code=$?
    trap - EXIT INT TERM HUP
    # Never leave client jobs running after Cloud SQL is stopped.
    for pid in "$MONITOR_PID" "$P1" "$P2"; do
        if [[ -n "$pid" ]]; then kill "$pid" >/dev/null 2>&1 || true; fi
    done
    if [[ "$SQL_TOUCHED" == 1 ]]; then
        echo 'Stopping Cloud SQL...'
        if ! sql_stop; then
            echo 'CRITICAL: SQL stop is UNCONFIRMED. Once internet is restored, run:' >&2
            echo "gcloud sql instances patch $SQL_INSTANCE --project=$PROJECT_ID --activation-policy=NEVER --quiet" >&2
            code=1
        fi
    fi
    if [[ "$GUARD_PAUSED" == 1 ]]; then
        if ! gcloud_check_retry bash "$HW5_DIR/resume_guard.sh"; then
            echo 'CRITICAL: Scheduler resume UNCONFIRMED. Once internet is restored, run:' >&2
            echo "gcloud scheduler jobs resume $SCHEDULER_JOB --location=$REGION --project=$PROJECT_ID" >&2
            code=1
        fi
    fi
    echo "Evidence directory: $RUN_DIR"
    exit "$code"
}
interrupt() {
    echo 'Interrupted; stopping both clients and restoring database safety.' >&2
    exit 130
}
trap finish EXIT
trap interrupt INT TERM HUP

# Mark restoration as required *before* the pause request. If the reply is lost,
# the pause may still have succeeded remotely.
GUARD_PAUSED=1
bash "$HW5_DIR/pause_guard.sh"
wait_sql_operations
SQL_TOUCHED=1
POLICY="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
  --format='value(settings.activationPolicy)')"
if [[ "$POLICY" != 'ALWAYS' ]]; then
    sql_operation "Starting Cloud SQL $SQL_INSTANCE" \
      gcloud sql instances patch "$SQL_INSTANCE" --project="$PROJECT_ID" \
      --activation-policy=ALWAYS --quiet
fi
SQL_STATE="$(gcloud sql instances describe "$SQL_INSTANCE" --project="$PROJECT_ID" \
  --format='value(state)')"
[[ "$SQL_STATE" == 'RUNNABLE' ]] || {
    echo "Database not ready: $SQL_STATE" >&2; exit 1
}

# Test GCS + Flask + database BEFORE measuring/snapshotting the clients.
URL="http://${SERVER_IP}:8080/0.html"
HTTP_CODE="$(curl -sS --max-time 45 -o /dev/null -w '%{http_code}' \
  -H 'X-country: USA' "$URL")"
[[ "$HTTP_CODE" == '200' ]] || {
    echo "Server health check failed (HTTP $HTTP_CODE)" >&2; exit 1;
}

START_ID="$("$PYTHON" "$HW5_DIR/request_statistics.py" --max-id)"
echo "$START_ID" > "$RUN_DIR/after_request_id.txt"
START_UTC="$("$PYTHON" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
echo "$START_UTC" > "$RUN_DIR/start_utc.txt"
START_EPOCH="$(date +%s)"

printf 'Two concurrent course clients: %s each, seed %s, server %s\n' \
  "$REQUESTS_PER_CLIENT" "$SEED" "$SERVER_IP"
printf 'Start request_id: %s | UTC: %s\n' "$START_ID" "$START_UTC"
printf 'Client logs will be saved in %s\n' "$RUN_DIR"

"$CLIENT_BIN" --domain "$SERVER_IP" --bucket none --webdir none \
  --num_requests "$REQUESTS_PER_CLIENT" --index 11999 --port 8080 \
  --random "$SEED" --timeout 30 \
  > "$RUN_DIR/client_1.log" 2>&1 &
P1=$!
"$CLIENT_BIN" --domain "$SERVER_IP" --bucket none --webdir none \
  --num_requests "$REQUESTS_PER_CLIENT" --index 11999 --port 8080 \
  --random "$SEED" --timeout 30 \
  > "$RUN_DIR/client_2.log" 2>&1 &
P2=$!

# Lightweight periodic heartbeat; the course client stays quiet on success.
"$PYTHON" "$HW5_DIR/progress_monitor.py" "$START_ID" "$P1" "$P2" "$HW5_DIR" &
MONITOR_PID=$!

STATUS1=0
STATUS2=0
wait "$P1" || STATUS1=$?
wait "$P2" || STATUS2=$?
kill "$MONITOR_PID" >/dev/null 2>&1 || true
MONITOR_PID=''
END_UTC="$("$PYTHON" -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
END_EPOCH="$(date +%s)"
echo "$END_UTC" > "$RUN_DIR/end_utc.txt"
echo "$STATUS1" > "$RUN_DIR/client_1.exit_code"
echo "$STATUS2" > "$RUN_DIR/client_2.exit_code"
END_ID="$("$PYTHON" "$HW5_DIR/request_statistics.py" --max-id)"
echo "$END_ID" > "$RUN_DIR/through_request_id.txt"
DURATION=$((END_EPOCH - START_EPOCH))
EXPECTED=$((2 * REQUESTS_PER_CLIENT))
printf 'Client exit codes: %s, %s | wall time: %ss\n' "$STATUS1" "$STATUS2" "$DURATION"
if [[ "$REQUESTS_PER_CLIENT" -lt 50000 && "$DURATION" -gt 0 ]]; then
    "$PYTHON" -c 'import sys; duration, count = map(int,sys.argv[1:]); print(f"Rough 50,000/client projection at this observed rate: {duration*50000/count/3600:.1f} hours (not a guarantee).")' "$DURATION" "$REQUESTS_PER_CLIENT"
fi

"$PYTHON" "$HW5_DIR/request_statistics.py" \
  --after-id "$START_ID" --through-id "$END_ID" --expected "$EXPECTED" \
  --json-out "$RUN_DIR/request_statistics.json" \
  | tee "$RUN_DIR/request_statistics.txt"

# Copy raw metrics BEFORE any VM cleanup. This is data retrieval, not VM setup.
METRICS_OK=1
if gcloud compute scp --project="$PROJECT_ID" --zone="$ZONE" --quiet \
    "$SERVER_VM:/tmp/hw5_timings.csv" "$RUN_DIR/timings_all.csv"; then
    "$PYTHON" "$HW5_DIR/filter_timings.py" \
      "$RUN_DIR/timings_all.csv" "$RUN_DIR/timings_run.csv" \
      --start-utc "$START_UTC" --end-utc "$END_UTC"
    "$PYTHON" "$HW5_DIR/analyze_timings.py" "$RUN_DIR/timings_run.csv" \
      | tee "$RUN_DIR/timing_summary.txt"
    TIMING_ROWS="$("$PYTHON" -c 'import csv,sys; print(sum(1 for _ in csv.DictReader(open(sys.argv[1], newline=""))))' "$RUN_DIR/timings_run.csv")"
    if [[ "$TIMING_ROWS" -eq 0 ]]; then
        echo 'WARNING: No timing records matched the run window; inspect server CSV and UTC clocks.' >&2
        METRICS_OK=0
    elif [[ "$TIMING_ROWS" -lt "$EXPECTED" ]]; then
        echo "WARNING: $TIMING_ROWS timing records for $EXPECTED client requests. Investigate missing data." >&2
        METRICS_OK=0
    fi
else
    echo 'WARNING: Timing CSV could not be retrieved; server VM is preserved for retry.' >&2
    METRICS_OK=0
fi

if [[ "$STATUS1" -ne 0 || "$STATUS2" -ne 0 ]]; then
    echo 'WARNING: At least one course client exited unsuccessfully.' >&2
    exit 1
fi
RECORDED="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["rows_matching_course_client_headers"])' "$RUN_DIR/request_statistics.json")"
if [[ "$RECORDED" -ne "$EXPECTED" ]]; then
    echo "WARNING: Expected $EXPECTED client requests but database has $RECORDED matching rows. Investigate before reporting completion." >&2
    exit 1
fi
if [[ "$METRICS_OK" != 1 ]]; then
    echo 'WARNING: Request counts match, but timing evidence is incomplete.' >&2
    exit 1
fi
printf '\nLOAD TEST PASSED: %s/%s client requests recorded, both clients exited 0; timing CSV retrieved.\n' "$RECORDED" "$EXPECTED"

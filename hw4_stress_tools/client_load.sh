#!/usr/bin/env bash
# Runs 1-4 copies of the professor's Linux client, all on the CLIENT VM.
# Each invocation is hard-limited by GNU timeout; HTTP 400 is NOT a capacity failure.
set -euo pipefail

CLIENTS="${1:?Usage: client_load.sh CLIENTS DURATION_SECONDS SERVER_INTERNAL_IP}"
DURATION="${2:?Missing duration}"
SERVER_IP="${3:?Missing server IP}"
PORT="${4:-8080}"

if ! [[ "$CLIENTS" =~ ^[1-4]$ ]]; then
  echo "Error: CLIENTS must be 1, 2, 3, or 4" >&2; exit 2
fi
if ! [[ "$DURATION" =~ ^[0-9]+$ ]] || (( DURATION < 1 || DURATION > 300 )); then
  echo "Error: DURATION must be 1-300 seconds" >&2; exit 2
fi
if ! [[ "$SERVER_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Error: SERVER_IP must be an IPv4 address" >&2; exit 2
fi
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "Error: PORT must be 1-65535" >&2; exit 2
fi
if [[ ! -x "$HOME/http-client" ]]; then
  echo "Error: $HOME/http-client is missing or not executable" >&2; exit 2
fi

WORKDIR="$HOME/hw4_stress"
mkdir -p "$WORKDIR"
PREFIX="$WORKDIR/stress_${CLIENTS}"
START=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
printf 'Starting %s provided HTTP client process(es) for %s seconds -> %s:%s\n' "$CLIENTS" "$DURATION" "$SERVER_IP" "$PORT"

for ((i=1; i<=CLIENTS; i++)); do
  (
    set +e
    # Use -v to print HTTP status lines; awk keeps only tiny status-count files,
    # avoiding huge HTML response logs during the long stress tests.
    PYTHONUNBUFFERED=1 timeout --signal=TERM --kill-after=5s "${DURATION}s" \
      "$HOME/http-client" -d "$SERVER_IP" -p "$PORT" -n 1000000 -i 11999 \
      -b none -w none -t 10 -v 2>"${PREFIX}_client_${i}.stderr" |
      awk '/^[1-5][0-9][0-9] / { n[$1]++ } END { for (k in n) printf "%s %d\n", k, n[k] }' \
        > "${PREFIX}_client_${i}.counts"
    # A GNU timeout exit status of 124 is EXPECTED after the requested duration.
    codes=("${PIPESTATUS[@]}")
    printf '%s\n' "${codes[0]}" > "${PREFIX}_client_${i}.exit"
  ) &
done

# Wait until every process has exited. Never launch the next concurrency level
# until all processes from the previous level have finished.
wait
END=$(date -u +'%Y-%m-%dT%H:%M:%SZ')

python3 - "$CLIENTS" "$DURATION" "$SERVER_IP" "$PREFIX" "$START" "$END" <<'PY'
import collections
import datetime
import json
import pathlib
import sys

n, duration, ip, prefix, start, end = sys.argv[1:]
n = int(n)
codes = collections.Counter()
per_client = []
for i in range(1, n + 1):
    counter_file = pathlib.Path(f"{prefix}_client_{i}.counts")
    exit_file = pathlib.Path(f"{prefix}_client_{i}.exit")
    err_file = pathlib.Path(f"{prefix}_client_{i}.stderr")
    counts = {}
    if counter_file.exists():
        for line in counter_file.read_text().splitlines():
            key, count = line.split()
            counts[key] = int(count)
            codes[key] += int(count)
    exit_code = int(exit_file.read_text().strip()) if exit_file.exists() else -1
    stderr_text = err_file.read_text(errors="replace") if err_file.exists() else ""
    per_client.append({
        "client_number": i,
        "exit_code": exit_code,
        "normal_timeout": exit_code == 124,
        "status_counts": counts,
        "stderr_excerpt": stderr_text[:1200],
    })

fmt = "%Y-%m-%dT%H:%M:%SZ"
run_seconds = (datetime.datetime.strptime(end, fmt) - datetime.datetime.strptime(start, fmt)).total_seconds()
result = {
    "clients": n,
    "requested_duration_seconds": int(duration),
    "elapsed_seconds": run_seconds,
    "server_ip": ip,
    "start_utc": start,
    "end_utc": end,
    "status_counts": dict(sorted(codes.items(), key=lambda x: int(x[0]))),
    "total_http_responses": sum(codes.values()),
    "successful_200": codes["200"],
    "expected_denied_400": codes["400"],
    "other_http_responses": sum(count for code, count in codes.items() if code not in ("200", "400")),
    "client_process_failures": sum(c["exit_code"] not in (0, 124) for c in per_client),
    "per_client": per_client,
}
output = pathlib.Path(f"{prefix}_summary.json")
output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "per_client"}, indent=2))
print(f"Detailed evidence: {output}")
PY

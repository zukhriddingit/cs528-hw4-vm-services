#!/usr/bin/env bash
# One-time / repeatable development helper; final grader deployment uses setup.sh.
set -Eeuo pipefail

PROJECT_ID="quick-formula-457319-q3"
INSTANCE="hw5-sql"
DB_USER="hw5_user"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
PASSWORD_FILE="$ROOT/.db_password"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON. Run: python3 -m venv hw5/.venv" >&2
  exit 1
fi
if [[ ! -s "$PASSWORD_FILE" ]]; then
  echo "Missing $PASSWORD_FILE. Generate a password first." >&2
  exit 1
fi
"$PYTHON" -c 'import google.auth, google.cloud.sql.connector, pg8000' || {
  echo 'Install first: hw5/.venv/bin/python -m pip install "cloud-sql-python-connector[pg8000]"' >&2
  exit 1
}

# All asynchronous operations are explicitly waited upon to avoid Cloud SQL HTTP 409.
wait_for_pending_operations() {
  local op
  while :; do
    op="$(gcloud sql operations list --project="$PROJECT_ID" --instance="$INSTANCE" \
      --filter='status!=DONE' --sort-by='~insertTime' --limit=1 --format='value(name)')"
    [[ -n "$op" ]] || break
    echo "Waiting for pending Cloud SQL operation: $op" >&2
    gcloud sql operations wait "$op" --project="$PROJECT_ID" --timeout=unlimited || true
  done
}

run_cloudsql_operation() {
  local label="$1"; shift
  local op
  echo "$label" >&2
  op="$("$@" --async --format='value(name)')"
  if [[ -z "$op" ]]; then
    echo "Cloud SQL did not return an operation ID for: $label" >&2
    return 1
  fi
  gcloud sql operations wait "$op" --project="$PROJECT_ID" --timeout=unlimited
}

stop_sql() {
  echo 'Ensuring Cloud SQL is stopped...' >&2
  wait_for_pending_operations
  local current
  current="$(gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
    --format='value(settings.activationPolicy)')"
  if [[ "$current" != 'NEVER' ]]; then
    run_cloudsql_operation 'Stopping database' \
      gcloud sql instances patch "$INSTANCE" --project="$PROJECT_ID" \
      --activation-policy=NEVER --quiet
  fi
  gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
    --format='table(name,state,settings.activationPolicy)'
}

cleanup() {
  local rc=$?
  trap - EXIT
  if ! stop_sql; then
    echo 'ERROR: Could not verify that Cloud SQL was stopped. Stop it manually!' >&2
    rc=1
  fi
  exit "$rc"
}
trap cleanup EXIT

wait_for_pending_operations

run_cloudsql_operation 'Starting database' \
  gcloud sql instances patch "$INSTANCE" --project="$PROJECT_ID" \
    --activation-policy=ALWAYS --quiet

state="$(gcloud sql instances describe "$INSTANCE" --project="$PROJECT_ID" \
  --format='value(state)')"
if [[ "$state" != 'RUNNABLE' ]]; then
  echo "Expected RUNNABLE, got $state; refusing to initialize" >&2
  exit 1
fi

# GCP-created built-in PostgreSQL users have CREATEDB, allowing us to
# create hw5_requests owned by hw5_user. This avoids PG16 public-schema privileges.
if ! gcloud sql users list --instance="$INSTANCE" --project="$PROJECT_ID" \
  --format='value(name)' | grep -qx "$DB_USER"; then
  run_cloudsql_operation 'Creating database user' \
    gcloud sql users create "$DB_USER" --instance="$INSTANCE" --project="$PROJECT_ID" \
    --password="$(cat "$PASSWORD_FILE")"
else
  echo "Database user $DB_USER already exists" >&2
fi

DB_PASS="$(cat "$PASSWORD_FILE")" "$PYTHON" "$ROOT/setup_schema.py"
echo 'Database initialization complete.' >&2

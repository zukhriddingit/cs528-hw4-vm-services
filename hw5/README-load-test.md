# Homework 5: two concurrent HTTP clients

From the repository root, use `caffeinate -i bash hw5/run_load.sh 100` for a 200-request pilot, and—only after the pilot works—`caffeinate -i bash hw5/run_load.sh 50000` for 100,000 requests. The code uses the professor's macOS `~/Downloads/http-client`; set `HTTP_CLIENT_BIN=/path/to/http-client` if different.

The script pauses the hourly SQL-stop scheduler, starts Cloud SQL and waits for the operation, verifies an HTTP 200, saves a baseline database request ID, starts **two clients simultaneously with the exact same seed (5285)**, and waits for both. It saves their exit codes/logs, the ending request ID and UTC timestamps, read-only SQL statistics, and server timing CSV. It then stops Cloud SQL and re-enables the guard even if the script fails or is interrupted normally.

The source CSV from the server is `/tmp/hw5_timings.csv`. The script copies it through `gcloud compute scp`, filters by precise run UTC timestamps, and summarizes header, storage, database, response-preparation, response-send, and overall times using the existing `analyze_timings.py`. The server-side response-send interval does not measure complete client download latency.

The SQL summary explicitly excludes incidental unrelated traffic that does not have all expected course-client demographic headers. The summary reports how many records were excluded. Query results only count HTTP requests actually stored in PostgreSQL; a network failure before logging cannot be counted as a processed request.

Evidence is saved in `hw5/evidence/load_<UTC>_<request-count>x2/`. Avoid adding the raw timing CSVs or any password to Git. Capture the final `request_statistics.txt` and `timing_summary.txt` for the report, and reconcile them with the two client logs. A CLI exit code of zero by itself does not prove all expected requests reached the server.

**Safety:** This test uses billable VM, Cloud SQL, and network resources. It may run for hours. The shutdown function must stay paused throughout the run. If the computer shuts down unexpectedly, manually resume `bash hw5/resume_guard.sh`, stop SQL with `gcloud sql instances patch hw5-sql --project=quick-formula-457319-q3 --activation-policy=NEVER --quiet`, and verify `STOPPED/NEVER`. Never run `cleanup.sh` until timing evidence has been retrieved from the VM; the normal load script stops SQL but preserves the VMs.

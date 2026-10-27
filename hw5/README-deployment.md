# CS 528 — Homework 5 infrastructure (grader entry points)

**Project:** `quick-formula-457319-q3` (hardcoded in `_common.sh`, *not* read from grader's current gcloud project).

**Region / zone:** `us-central1` / `us-central1-a`.

This folder contains the grader entry points `setup.sh`, `startup.sh`, and `cleanup.sh`, a reporter VM startup script, the schema verification helper, and the hourly stop function. The two application source files must already be in `service1/app.py` and `service2/main.py`. The reporter application was copied **unchanged** from Homework 4.

## From a fresh clone

```bash
chmod +x hw5/*.sh
bash -n hw5/*.sh
python3 -m py_compile hw5/{setup_schema.py,check_schema.py,service1/app.py,sql_monitor/main.py}
# Provision real billable cloud infrastructure only when you're ready:
bash hw5/setup.sh
```

`setup.sh` creates/reuses `hw5-sql` (Enterprise PostgreSQL 16), starts it if stopped, ensures DB user `hw5_user` and tables `requests`, `failed_requests`, creates/reuses a Secret Manager password, configures IAM/VMs/PubSub, and deploys an authenticated hourly Cloud Function / Scheduler job. It creates `hw5-server` (e2-standard-2) and `hw5-reporter` (e2-micro) with `--metadata-from-file` for the startup scripts. Existing VMs are reset to pick up updated startup metadata; package installations are skipped on reboot using lock files.

It reserves `hw5-server-ip` and then **looks up** its actual IP dynamically; no IP literal is hardcoded. Old HW4 VMs, IPs, and the shared HW3 topic are left untouched.

The supplied course client is a macOS ARM64 executable, not a Linux client. Run **two concurrent client processes on your Mac**, using identical `--random` values, 50,000 requests each. Confirm the client's accepted flag syntax first. A Linux client VM is therefore *not* provisioned by this setup.

## After setup

Query your public server IP (the script prints it already):

```bash
gcloud compute addresses describe hw5-server-ip \
  --region=us-central1 --project=quick-formula-457319-q3 \
  --format='value(address)'
```

Inspect VM startup progress **without SSH commands**:

```bash
gcloud compute instances get-serial-port-output hw5-server \
  --project=quick-formula-457319-q3 --zone=us-central1-a --port=1
```

Basic curl test, with `$IP` set to the dynamically fetched address:

```bash
curl -i "http://$IP:8080/0.html" -H 'X-country: USA' -H 'X-gender: Male' \
  -H 'X-age: 26-35' -H 'X-income: 40k-60k' \
  -H 'X-client-IP: 203.0.113.42' -H 'X-time: 2026-10-27T10:30:00Z'
curl -i "http://$IP:8080/999999.html" -H 'X-country: USA'
curl -i "http://$IP:8080/0.html" -H 'X-country: Iran'
```

For the report, display table contents before and after each request using a SQL query (we'll prepare this step separately). Also collect timing CSV from server via `gcloud compute scp` or a VM transfer as permitted by the assignment.

## Safety schedule for long stress tests

Cloud Scheduler calls `hw5-sql-guard` hourly at minute 0 UTC; the function requests a stop if the instance is running. When actively performing a stress run lasting more than one hour, pause **only the scheduler**:

```bash
bash hw5/pause_guard.sh
# Run tests while SQL is on
bash hw5/resume_guard.sh
```

When not working, **always** re-enable the job. `setup.sh` temporarily pauses it while configuring resources, then resumes it, including when setup fails.

## Cleanup (noninteractive / grading)

After collecting evidence and your timings, run:

```bash
bash hw5/cleanup.sh
```

This stops Cloud SQL and waits for the operation to finish; it **never deletes the database or its contents**. It removes both HW5 VMs, reserved IP, firewall rule, HW5 subscription, Scheduler job and stop function. To deploy the guard again, rerun `setup.sh`. It preserves the shared HW3 Pub/Sub topic, HW4 infrastructure, credentials in Secret Manager, and HW5 IAM service accounts for re-use. It does not use `gcloud auth application-default login`, so no ADC revocation is required.

**Billing:** Cloud SQL compute charges stop when state is `STOPPED`; disk and IP-related charges can continue. Stopped Compute Engine VMs still have persistent-disk charges; cleanup **deletes** the HW5 VMs and static IP. The HTTP endpoint is temporarily public on port 8080. Production systems would use private networking, authentication, and tighter firewall rules.

**Validation limits:** All scripts can be linted/compiled offline, but actual IAM/Cloud Functions/VM integration must be verified in your project after provisioning. The grader must have permission to create resources, deploy functions, and attach the specified service accounts.

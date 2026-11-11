# CS 528 - Homework 8: Zone-aware, fault-tolerant web server

Two independently running Flask/Gunicorn servers are deployed to separate Google Cloud zones, with a **regional external passthrough Network Load Balancer** handling TCP port 8080. The same HW4 server still serves HTML from GCS and handles HW3-style requests; the new `X-Server-Zone` header identifies the responding VM.

## Architecture

Client (one request/second) -> regional forwarding rule / public IPv4 -> TCP regional backend service -> unmanaged instance groups (`hw8-ig-a`, `hw8-ig-b`) -> `hw8-server-a` in `us-central1-a`, `hw8-server-b` in `us-central1-b`.

Health check: `GET /health` at port 8080, every 5 seconds, 2-second timeout, healthy/unhealthy threshold 2. The server uses the Compute Engine instance metadata server to read its zone. The health endpoint doesn't fetch GCS data, so it remains independent of a large object download.

## Project requirements and reuse

- Project: `quick-formula-457319-q3`
- Dataset: `gs://cloud_hw2_bucket/pages/*.html` (from HW2)
- Reused IAM service account: `hw4-web-sa@quick-formula-457319-q3.iam.gserviceaccount.com`. Its HW4 roles allow reading bucket objects, writing Cloud Logging entries and publishing HW3 forbidden-country Pub/Sub events.
- Default VPC network, public service port `8080`. Firewall rule `hw8-allow-web` currently allows TCP 8080 from all IPv4 clients, including Google's health-checkers. Use only as temporary coursework infrastructure.
- Python packages are in `server/requirements.txt`; servers use Debian 12, `e2-micro`, 10 GB `pd-standard` disks.

The setup script assumes that the HW4 service account and GCS object already exist and will stop otherwise; it does **not** create, delete, or modify shared HW2/HW3/HW4 data.

## Deploy

1. Install/authenticate the Google Cloud CLI and select a project with Compute Engine permissions.
2. Run from **this `hw8` directory**:

```bash
bash scripts/setup.sh
```

3. Allow the VM startup scripts to install dependencies; then verify both VMs:

```bash
gcloud compute ssh hw8-server-a --zone=us-central1-a --command='sudo systemctl is-active hw8-server'
gcloud compute ssh hw8-server-b --zone=us-central1-b --command='sudo systemctl is-active hw8-server'
gcloud compute backend-services get-health hw8-backend --region=us-central1
```

4. Obtain the dynamic load-balancer IP after deployment:

```bash
LB_IP=$(gcloud compute addresses describe hw8-lb-ip --region=us-central1 --format='value(address)')
curl -i "http://$LB_IP:8080/health"
curl -i "http://$LB_IP:8080/0.html"
```

Both responses should return HTTP 200 and `X-Server-Zone`. If either VM is unhealthy, inspect its service and startup logs:

```bash
gcloud compute ssh hw8-server-a --zone=us-central1-a --command='sudo journalctl -u hw8-server -n 80 --no-pager'
gcloud compute instances get-serial-port-output hw8-server-a --zone=us-central1-a --port=1 | tail -n 60
```

## Client and supported requests

The client uses Python's standard library only and records UTC request-start timestamps, latency, HTTP status, zone and errors. All completed HTTP 200 bodies count as successful; a response-body read timeout counts as an error even if headers had HTTP 200.

```bash
python3 client.py --url "http://$LB_IP:8080/0.html" --count 30 --out results/baseline.csv
python3 client.py --url "http://$LB_IP:8080/health" --count 75 --out results/recovery.csv
python3 client.py --url "http://$LB_IP:8080/" --method POST --file 0.html --count 5 --out results/post.csv
python3 client.py --url "http://$LB_IP:8080/0.html" --method PUT --count 1 --out results/put.csv
python3 client.py --url "http://$LB_IP:8080/0.html" --country Iran --count 1 --out results/forbidden.csv
```

Note: The original HW3/HW4 web server answers PUT with HTTP 501 and forbidden-country requests with HTTP 400. These are expected HTTP results, not transport failures. The original GCS-backed route continues to support GET/POST file requests.

## Failover measurement

1. Confirm both backends are `HEALTHY`; run `client.py` from Terminal 1 with `--count 90 --url "http://$LB_IP:8080/0.html" --out results/failover.csv`.
2. After ~10 requests, stop only server A **in Terminal 2**, and record the server's UTC clock:

```bash
gcloud compute ssh hw8-server-a --zone=us-central1-a --command='sudo systemctl stop hw8-server; printf "STOP_COMPLETED_UTC="; date -u +%Y-%m-%dT%H:%M:%S.%3NZ'
```

3. Run `gcloud compute backend-services get-health hw8-backend --region=us-central1`; expect A `UNHEALTHY`, B `HEALTHY`.
4. Compare the recorded stop-completion timestamp to the last connection-refused error and the first sustained successes through B. Separately count *all* failures and body-read timeouts. Do not infer an exact health-check transition time without a log of the transition.

## Recovery measurement

1. With only B healthy, run `python3 client.py --url "http://$LB_IP:8080/health" --count 75 --out results/recovery.csv`.
2. Restart A after several requests:

```bash
gcloud compute ssh hw8-server-a --zone=us-central1-a --command='sudo systemctl start hw8-server; printf "START_COMPLETED_UTC="; date -u +%Y-%m-%dT%H:%M:%S.%3NZ'
```

3. Record the first successful A response timestamp; its difference from the start-completion timestamp is **client-observed recovery time**, not exact internal health-check transition time. Confirm both backends return `HEALTHY`.

## Observed results, November 11, 2026 UTC

| Measurement | Observed result |
| --- | --- |
| Baseline GET `/0.html` | 30/30 complete successes: A 15, B 15 (50% each) |
| Failure, stop completed | 02:11:20.938 UTC |
| Last connection-refused error | 02:11:40.552 UTC (19.614 s after stop) |
| Failover GET `/0.html` | 5 complete A, 67 complete B, 18 failed/incomplete |
| Later failover timeout | Error at 02:12:28.772 UTC (67.834 s after stop); all errors did **not** cease within 60 s |
| Recovery, start completed | 02:20:43.901 UTC |
| First post-restart A response | 02:21:08.632 UTC (24.731 s observed recovery) |
| Recovery GET `/health` | 75/75 complete successes, A 15 and B 60 |
| After A's first return | A 15 / B 13 (53.6% / 46.4% among remaining 28 requests) |

See `results/observed_summary.md`. The user's raw baseline/failover/recovery CSVs are retained locally and should be included in the submission.

**Interpretation:** A network passthrough load balancer distributes TCP connections according to a hash/connection tracking, not strict per-request round-robin. Baseline and post-recovery ratios are samples, not guaranteed service ratios. The late failover timeouts on the 97,586-byte GCS-backed route are unresolved; the later direct-to-B and through-LB `/health` probes returned HTTP 200.

## Archive and cleanup

Before teardown:

```bash
bash scripts/snapshot.sh
```

After verifying your CSV files and report are saved:

```bash
bash scripts/cleanup.sh --yes
```

The cleanup script deletes only `hw8-*` infrastructure, including the forwarding rule, reserved IP, instance groups, health check, backend service, VMs/disks and firewall. It deliberately leaves HW2 pages, HW3 Pub/Sub, HW4 IAM, and your local results untouched. Deleting cloud resources may still leave accrued usage charges.

## Google Cloud references

- https://docs.cloud.google.com/load-balancing/docs/network/setting-up-network-backend-service
- https://docs.cloud.google.com/load-balancing/docs/network/networklb-backend-service
- https://docs.cloud.google.com/load-balancing/docs/network/ext-netlb-traffic-distribution

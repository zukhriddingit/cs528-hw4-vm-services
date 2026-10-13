# CS 528 — HW4: VM-based HTTP service

Adapted from HW3 (Flask serving files from Cloud Storage, Pub/Sub forbidden-country reporting).

**Project:** `quick-formula-457319-q3`  
**Bucket:** `gs://cloud_hw2_bucket/pages/`  
**Deployment:** 3 VMs, where service 1 is on `hw4-server` (e2-micro, static IP), service 2 is on `hw4-reporter` (e2-micro), and clients run on `hw4-client` (e2-small).

## 1. Before deploying

Run these commands in a terminal with `gcloud`:

```bash
gcloud auth list
gcloud config set project quick-formula-457319-q3
gcloud storage ls gs://cloud_hw2_bucket/pages/0.html
gcloud compute zones list --filter='name=us-central1-a'
```

Your account must be allowed to create VMs, assign service accounts, modify IAM policies, and reserve IP addresses.

## 2. Deploy all three VMs in one command

In the extracted repository root:

```bash
chmod +x scripts/*.sh
PROJECT_ID=quick-formula-457319-q3 ZONE=us-central1-a ./scripts/deploy.sh
```

The script creates least-privilege service accounts; a dedicated `hw4-forbidden-sub` Pub/Sub subscription on the existing `hw3-forbidden-requests` topic (so the HW3 consumer cannot steal messages); Pub/Sub bindings; an ephemeral public client and reporter IP; a **reserved static IP** for the server; a port 8080 firewall rule; 3 VMs; and startup metadata containing program source. VM boot scripts install dependencies and start `systemd` services. **No service account key files** are needed.

If the bucket cannot be found or the project denies IAM/Compute Engine permissions, stop and fix the error before retrying.

Check after the startup scripts finish:

```bash
gcloud compute ssh hw4-server --zone=us-central1-a --command='sudo systemctl status hw4-server --no-pager'
gcloud compute ssh hw4-reporter --zone=us-central1-a --command='sudo systemctl status hw4-reporter --no-pager'
gcloud compute instances list --filter='name~^hw4-'
```

Get IP (run in Mac terminal):

```bash
IP=$(gcloud compute addresses describe hw4-server-ip --region=us-central1 --format='value(address)')
echo "http://$IP:8080/0.html"
```

## 3. Verify response codes and Pub/Sub

```bash
curl -i "http://$IP:8080/0.html"                                             # 200
curl -i -X POST "http://$IP:8080/" -H 'Content-Type: application/json' -d '{"file":"0.html"}'   # 200
curl -i "http://$IP:8080/999999.html"                                        # 404
curl -i -X PUT "http://$IP:8080/0.html"                                       # 501
curl -i -X TRACE "http://$IP:8080/0.html"                                     # 501
curl -i -H 'X-country: Iran' "http://$IP:8080/0.html"                         # 400 + CRITICAL + Pub/Sub
```

`X-country` is the same **simulation** used in HW3; it is not verified geolocation. Document this assumption in the report.

```bash
gcloud logging read 'logName="projects/quick-formula-457319-q3/logs/hw4-web" AND severity>=WARNING' --limit=20 --format=json
gcloud compute ssh hw4-reporter --zone=us-central1-a --command='sudo journalctl -u hw4-reporter -n 40 --no-pager'
```

Cloud Logging entries must contain `jsonPayload` and `severity` WARNING (404), ERROR (501), and CRITICAL (country).

In browser, take screenshots of 200 and 404. For 501, enter this JavaScript in DevTools Console while on the same IP:

```javascript
fetch('/0.html', {method:'PUT'}).then(r => console.log('PUT status:', r.status, r.statusText))
fetch('/0.html', {headers:{'X-country':'Iran'}}).then(r => console.log('Forbidden status:', r.status, r.statusText))
fetch('/', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({file:'0.html'})}).then(r => console.log('POST status:', r.status, r.statusText))
```

Capture the browser DevTools Network tab for the 501, 400, and POST responses (a normal address bar only sends GET). The denied-country case must show 400.

## 4. Client VM and 100 requests

Check the supplied binary format before copying it to a Linux VM:

```bash
file /path/to/http-client
```

If the file is `Mach-O` (macOS), it **cannot run natively on a Debian VM**; obtain the Linux build or source code from the course, then copy the compatible client to `hw4-client`. If ELF Linux, upload it:

```bash
gcloud compute scp /path/to/http-client hw4-client:~/http-client --zone=us-central1-a
gcloud compute ssh hw4-client --zone=us-central1-a --command='chmod +x ~/http-client; file ~/http-client'
```

The client command-line arguments depend on the professor's binary. Confirm its `--help`/instructions before the 100-request and stress runs.

## 5. Stress test + graphs

- Use **only one** smallest server VM, and 1 / 2 / 3 / 4 client processes on the *separate* client VM (never more than four).
- Measure each concurrency setting for **180 seconds** using the bundled lightweight sampler. Record counts of successful 200 and failed responses, excluding intentional 400 permission-denied responses.
- The metric CSV contains CPU utilization (%), memory utilization (%), used memory (MB), network ingress/egress (Mbps). Create a graph using averages or peaks for 1–4 concurrent clients (clearly label which).

Start collection on the server (example for one run):

```bash
gcloud compute ssh hw4-server --zone=us-central1-a --command='sudo /opt/hw4/server/.venv/bin/python /opt/hw4/server/collect_metrics.py --duration 180 --output /tmp/hw4_metrics_1.csv'
```

Start the relevant number of clients while that sampler is running, then use SCP to retrieve the four CSV files:

```bash
gcloud compute scp hw4-server:/tmp/hw4_metrics_1.csv ./metrics_1.csv --zone=us-central1-a
```

Do not invent stress results. Cost should come from the actual Billing report or be explicitly labeled an estimate if the live billing page lags. Record start/stop times and configuration for estimation.

## 6. Submission checklist

- [ ] Repo contains service1, service2, `scripts/deploy.sh`, both startup scripts, and `scripts/cleanup.sh`.
- [ ] VM instances screenshot + static IP + service accounts/IAM screenshot.
- [ ] Browser: GET 200, non-existent 404, unsupported method 501 (Network tab), and ideally POST 200 / forbidden 400.
- [ ] Cloud Logging screenshot/JSON: 404 WARNING, 501 ERROR, country CRITICAL.
- [ ] Second VM stdout screenshot: forbidden request.
- [ ] Client VM demonstrates 100 HTML file requests.
- [ ] Stress test 1–4 client instances (<=4), 3-minute measurements for each, status/errors, cost, plotted CPU, memory and bandwidth.
- [ ] GitHub URL and completed PDF report.

## 7. Stop charges

**After saving evidence and screenshots**, run:

```bash
./scripts/cleanup.sh
```

This removes the 3 VMs, the reserved IP, firewall rule, and the HW4-only Pub/Sub subscription. It intentionally preserves the SAs and the pre-existing Pub/Sub topic (because it belongs to your HW3 work); delete HW4-only resources later if no longer needed.

### Design notes

- Server VM IAM: bucket-only `roles/storage.objectViewer`, project `roles/logging.logWriter`, topic-only `roles/pubsub.publisher`.
- Reporter VM IAM: `roles/pubsub.subscriber` on the HW4-only `hw4-forbidden-sub` subscription (avoids racing against old HW3 consumers).
- Client VM service account: no IAM roles.
- All VMs have `cloud-platform` OAuth scope, but effective access is limited by IAM. This matches Google guidance.
- Unlike your HW3 reporter, this VM reporter prints to its journal but no longer appends to the bucket. HW4 only requires standard output; removing bucket writes avoids extra permissions, cost, and read/modify/write races.
- Server port 8080 is open to the internet for coursework testing. Destroy the firewall rule during cleanup; never use this unauthenticated example for sensitive real-world files.

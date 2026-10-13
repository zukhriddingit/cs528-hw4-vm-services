# CS528 HW4 — controlled performance stress test

This small toolkit uses the **professor's supplied Linux HTTP client** on your existing `hw4-client` VM. It does **not** create or resize VMs or grant permissions.

## Prerequisites

- Your Mac is authenticated with `gcloud` in project `quick-formula-457319-q3`.
- `hw4-server` (e2-micro) and `hw4-client` (Ubuntu 24.04) are RUNNING in `us-central1-a`.
- The professor's Linux executable is available as `~/http-client` on `hw4-client` (it should pass `~/http-client --help`).
- Server startup installed `/opt/hw4/server/collect_metrics.py` and its Python `psutil` package.
- The web server responds with HTTP 200 at `http://SERVER_INTERNAL_IP:8080/0.html`.

## Run the whole experiment

From your **Mac** terminal, in the parent folder of `hw4_stress_tools`:

```bash
python3 hw4_stress_tools/run_stress.py
```

The script discovers the internal server IP and runs **sequential** experiments with 1, then 2, then 3, then 4 concurrent client processes. Each experiment continuously generates requests for **180 seconds**. At most four processes run simultaneously; no new VMs are created. Each client process is hard-limited to 180 seconds.

It also starts a separate 1 Hz CPU/memory/network sampler on the **server VM** for every experiment. Sampling spans the entire load interval; the analysis restricts metrics to the request time window.

To run only one concurrency level first:

```bash
python3 hw4_stress_tools/run_stress.py --levels 1
```

Then, when that works, run the rest without repeating the first:

```bash
python3 hw4_stress_tools/run_stress.py --levels 2 3 4
```

You can also explicitly set `--project`, `--zone`, and `--output`.

## Outputs

Files are saved under `./hw4_stress_results/`:

- `metrics_1.csv` ... `metrics_4.csv` — raw server-side 1 Hz measurements.
- `summary_1.json` ... `summary_4.json` — request counts and client exits.
- `stress_summary.csv` — per-level averages, peaks, response outcomes, and sampling counts.
- `cpu_usage.svg`, `memory_usage.svg`, `bandwidth_usage.svg` — actual graphs, generated without extra Python packages.
- `collector_1.log` ... `collector_4.log` — sampler terminal output and troubleshooting.

**Important:** A status `400` is only excluded from *capacity failure* counts when it's the expected permission-denied response (as confirmed in earlier test logs). HTTP 500/503, network/timeout errors, or unexpected client process exits can indicate stress-induced failures.

The data measure **whole-server-VM CPU/memory/network** utilization; don't label them as just the Gunicorn process. Bandwidth is the sum of VM ingress and egress (`rx_mbps + tx_mbps`), in megabits per second. `e2-micro` is a **shared-core** VM, so guest-reported CPU percentages should not be interpreted as the capacity of two dedicated physical cores.

## Cost + cleanup

While three VMs run, normal Compute Engine billing, boot disks, external IPs, and storage/network operations may incur charges. The tool does not stop or remove resources when finished, because you still need to collect browser and logging screenshots. Once all evidence is saved, run your HW4 `scripts/cleanup.sh` to delete the three VMs, static IP, firewall rule and HW4 subscription. **Do not delete your HW2 bucket.**

For the required actual expense, consult Google Cloud Billing for the project and the test's date/time range; billing data may be delayed. Do not represent an estimate as the actual charged amount.

## Technical notes

- The provided HTTP client is invoked with `-d SERVER_INTERNAL_IP -p 8080 -n 1000000 -i 11999 -b none -w none -t 10 -v`. The large `-n` ensures the load lasts until GNU `timeout` stops the process at 180 seconds.
- `-v` produces the response status and body, but only status counts are retained by `awk`, so VM disks are not flooded with HTML responses.
- Process exit code `124` is normal because GNU `timeout` stops the clients at their three-minute limit.
- The sampler starts slightly before the load and stops slightly after it; analysis filters samples to recorded client start/end timestamps.
- The client script stores its tiny status summaries at `~/hw4_stress/` on `hw4-client`.

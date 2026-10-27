# CS528 HW5: Dedicated client VM (no Mac Wi-Fi dependency)

Run from the existing HW5 Git checkout on your Mac:

```bash
unzip -o ~/Downloads/hw5_vm_load_migration.zip -d .
bash hw5/launch_vm_load.sh 50000
```

* Provisions `hw5-client` (Debian 12, e2-standard-2) via a VM startup script.
* Extracts the exact Python 3.12 main bytecode from the professor's macOS ARM64 PyInstaller `~/Downloads/http-client`. The original Python code, seed, arguments, and request generation are retained; it is **not** a substitute client. Python 3.12 executes this bytecode in the official Linux Docker image.
* Starts **two** concurrent processes each sending 50,000 requests using seed 5285 over the internal VPC IP of `hw5-server`.
* The VM pauses the hourly shutdown job, starts Cloud SQL, measures before/after request IDs, queries statistics, stops SQL, resumes the scheduler, uploads results to GCS, and shuts itself down.
* `hw5/.course_client_312.b64` is a local derived version of the supplied course binary; exclude it from Git. The professor has the original binary and can regenerate this file using `extract_course_client.py`.

**Monitor** (after the startup package installation):

```bash
gcloud compute ssh hw5-client --project=quick-formula-457319-q3 --zone=us-central1-a --command='sudo tail -n 40 /var/log/hw5-load.log'
```

If VM bootstrap fails before the logfile exists:

```bash
gcloud compute instances get-serial-port-output hw5-client --project=quick-formula-457319-q3 --zone=us-central1-a --port=1 | tail -n 65
```

If the VM has stopped, the run has ended or failed. Verify the Cloud SQL state and Cloud Scheduler status from your Mac. The VM's evidence is uploaded to:

```text
gs://cloud_hw2_bucket/hw5_evidence/vm_load_<UTC>_50000x2/
```

Download to your Mac with `gcloud storage cp --recursive` once you reconnect. Save `request_statistics.json`, `start_utc.txt`, `end_utc.txt`, `after_request_id.txt`, `through_request_id.txt`, and client logs. Timing CSV is still on `hw5-server` at `/tmp/hw5_timings.csv` and can be retrieved via `gcloud compute scp` after the run using the existing filter script and UTC bounds. The provided report generator may expect filenames under `hw5/evidence/load_*_50000x2`; copy the VM evidence into an appropriately named local run folder before running it.

Do not close your Mac immediately after VM creation: first confirm that `hw5-load.service` has begun running, then you can disconnect.

To delete client VM and disk **after evidence is saved**, run `gcloud compute instances delete hw5-client --project=quick-formula-457319-q3 --zone=us-central1-a --quiet`, or use the patched `hw5/cleanup.sh` after all experiments. Do not delete `hw5-sql`; stop it with activation policy `NEVER`.

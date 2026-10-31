# CS 528 - Homework 6

**Author:** Zukhriddin Fakhriddinov  
**GCP project:** `quick-formula-457319-q3`  
**Existing Cloud SQL:** `hw5-sql` / database `hw5_requests`  
**Cloud bucket:** `gs://cloud_hw2_bucket`

## Results from the completed run (2026-10-31)

| Model | Held-out rows | Test accuracy | Goal |
|---|---:|---:|---|
| Client IP -> Country (nearest IP) | 26,420 | **100.00%** | >=99% met |
| Request features -> Income (CatBoost depth 6) | 26,461 | **33.5891%** | ~35% aim, below target |

The income model beat the majority-class baseline of **12.5959%**. The
income target was not achieved; the PDF explains the random-seed and
feature-signal limitations. These are **real results read back from GCS**, not
simulated demo outputs.

**Full output prefix:**
`gs://cloud_hw2_bucket/hw6/runs/20261031T162052Z/`

Contains `country_test_predictions.csv`, `income_test_predictions.csv`,
`country_metrics.json`, `metrics.json`, `status.json`, and VM logs. Do not
rerun merely to recreate already saved results.

## Third normal form

`step2_3nf.sql` and `step2_migrate.py` migrate HW5 `requests` into:

- `hw6_countries`: country and banned flag (country -> is_banned)
- `hw6_ip_country`: source IP mapping (client_ip -> country_id)
- `hw6_requests`: individual request facts only
- `hw6_request_country_exceptions`: sparse record preserving a conflicting
  legacy IP (203.0.113.42/32 appeared with both Iran and USA)

Original `requests` and `failed_requests` tables are preserved. Verified
132,330 migrated requests, 198 countries, 98,521 unique IPs and no mismatches.

## Reproduce

Prerequisites: authenticated `gcloud` CLI, access to the existing HW5
PostgreSQL 16 instance and the saved Secret Manager secret
`hw5-db-password`. The migration uses the HW5 Python environment
(`hw5/.venv/bin/python`) and existing HW5 `setup_schema.py`.

From the GitHub repository root:

```bash
# Required only to create or refresh normalized tables:
bash hw6/step2_migrate.sh

# End-to-end VM ML job. Starts SQL, provisions VM, trains,
# uploads predictions, prints results from GCS, deletes VM, stops SQL.
bash hw6/step3_run_vm.sh

# Check completed run output without any compute resources:
gcloud storage ls gs://cloud_hw2_bucket/hw6/runs/20261031T162052Z/
```

`step3_run_vm.sh` automatically creates/reuses the VM service-account IAM
bindings, uploads a job bundle, launches a short-lived e2-standard-2 Debian 12
VM, polls Cloud Storage for completion, prints `metrics.json` and predictions
from GCS, then performs guarded cleanup. Cloud SQL startup and shutdown are
operation-safe; the script waits for pending operations and uses an EXIT trap.

**Cleanup observed in completed run:** VM deleted and Cloud SQL `STOPPED / NEVER`.

## Source files

- `step2_3nf.sql`: 3NF DDL and data-transfer queries
- `step2_migrate.py`: transaction and migration verification
- `step2_migrate.sh`: controlled start / migrate / stop wrapper
- `step3_models.py`: two models, validation splits, test evaluation,
  output exports
- `step3_vm_startup.sh`: VM initialization, Python installation,
  code execution, logs, status marker
- `step3_run_vm.sh`: complete cloud orchestration and cleanup
- `requirements_step3.txt`: exact Python packages
- `CS528_HW6_Report.pdf`: final assignment report

Do not commit local database passwords, `.env`, virtual environments, or
raw user credentials. The course data are synthetic HTTP-client headers.

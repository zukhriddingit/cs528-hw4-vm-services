# CS 528 HW6 — Final model-run reliability fix (Oct 31, 2026)

## Why the preceding run failed

The October 31 VM successfully loaded all 132,330 normalized rows. Country prediction produced 100.00% accuracy on a **26,420-row IP-group-held-out test**, but the program did not upload its CSV yet. The first income CatBoost depth-6 candidate finished after 858.4 seconds, with **33.96% validation accuracy**. The old code then fit a second, deeper model, and the outer 40-minute orchestration timer destroyed the VM before both outputs were uploaded. This is an orchestration/runtime problem, not a database or IAM failure.

## Fixes in this ZIP

1. Fit only the previously successful **CatBoost depth-6 / 500-iteration** configuration for income. Its validation score is not an income test score.
2. Upload `country_test_predictions.csv` and `country_metrics.json` **immediately after** finishing the country model. These files survive if income training fails later.
3. After income fitting, upload `income_test_predictions.csv` and the full `metrics.json`.
4. If income training raises an exception, upload `metrics_partial.json` containing the country result and the error.
5. Include country checkpoint and partial metrics in timeout diagnostics.
6. Retain the existing GCP service-account permissions, VM startup logs, safe SQL operations and cleanup behavior.

## Run from your local `Cloud HWs` directory

```bash
unzip -o ~/Downloads/CS528_HW6_step3_efficient.zip -d .
caffeinate -i bash hw6/step3_run_vm.sh
```

The script expects SQL to be initially STOPPED/NEVER. It preserves the existing HW5 database; creates only a short-lived VM; uses the normalized `hw6_*` tables; saves output to a fresh `gs://cloud_hw2_bucket/hw6/runs/<UTC run ID>/` prefix; and attempts to delete its VM and stop the SQL instance on exit.

If it times out again, check the new run prefix printed in your terminal and read the latest `models.log` or `country_metrics.json` from the bucket. **Do not manually patch the database while an operation is pending.**

## IMPORTANT: interpretation

- `100.00%` is the *previous run's* country held-out test metric. A rerun will remeasure it and save actual predictions.
- `33.96%` is the *previous run's income validation accuracy*, **not** an income test score. A rerun must report the untouched income test accuracy.
- A score below the assignment's suggested 35% target must be reported accurately, along with possible causes. No guarantee of 35% is made.
- This package was syntax-checked and its success/failure output paths tested locally using synthetic data and simulated uploads; the actual GCP end-to-end training still requires a live run.

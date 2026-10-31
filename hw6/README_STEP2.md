# CS 528 HW6 — Step 2: 3NF normalization

This package adds files to your existing `Cloud HWs/hw6/` folder.
It **does not overwrite** the working Step 1 scripts.

## Running

From `Cloud HWs/` after extracting the ZIP here:

```bash
bash hw6/step2_migrate.sh
```

The shell wrapper reuses the operation-safe Cloud SQL start/stop logic from
Step 1. The database must start in `STOPPED / NEVER` state. The Python script
finds `setup_schema.py` from either `HW5/hw5` (your current layout) or `hw5`.
The Python environment is `hw5/.venv` (your current layout).

**This step writes new tables in the *existing* `hw5_requests` database.** It
never drops, truncates, or changes the HW5 `requests` and `failed_requests`
tables. The migration is an atomic transaction, so an error rolls it back.
It can be safely rerun; it recreates the contents of its four `hw6_*` tables.
Do not run it concurrently with other HW6 jobs that rely on those tables.

## Normalized schema

- `hw6_countries(country_id PK, country UNIQUE, is_banned)` stores each country's
  ban status once, enforcing the functional dependency `country -> is_banned`.
- `hw6_ip_country(client_ip PK, country_id FK)` stores the dominant country
  for each IP once, enforcing `client_ip -> country_id` for clean data.
- `hw6_requests(request_id PK, client_ip FK, gender, age, income,
  time_of_day, requested_file, status_code, received_at)` stores request facts.
- `hw6_request_country_exceptions(request_id PK/FK, original_country_id FK)`
  records the *original* country when a request disagrees with the IP's
  dominant country. It also represents original `NULL` when necessary.

The original `failed_requests` table is preserved because it is already a
standalone table with a primary key and no known transitive dependencies, and
HW5 did not include a reliable key linking a failure to a request row.

## Why the exception table matters

The Step 1 audit found one conflicting IP:
`203.0.113.42` was associated with both `USA` and `Iran`. The assignment's
functional dependency is thus not perfectly satisfied by the raw HW5 data.
This script uses the most frequent country per IP as its canonical mapping,
with a deterministic tie-breaker. It **does not** silently discard the
inconsistent request(s): sparse exceptions preserve every original country.

The migration verifies that all HW5 request records survive and that country,
ban status and all non-country request fields match exactly. The original tables
are never altered. Query results are also written to `hw6/step2_3nf_output.txt`.

## Next step

Build the two models on the normalized tables and upload independent test-set
predictions to `gs://cloud_hw2_bucket/hw6/`. Actual model performance must be
measured; a 99% accuracy claim on reused IPs is NOT evidence of 99% accuracy
on previously unseen IPs. Income labels have eight near-uniform classes,
so the observed majority-class baseline is approximately 12.6%.

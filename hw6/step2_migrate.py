#!/usr/bin/env python3
"""CS528 HW6: preserve HW5, create a lossless third-normal-form copy.

Runs entirely in one PostgreSQL transaction. Aborts on invalid assumptions.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The user's actual layout contains HW5/hw5/setup_schema.py, with an isolated
# Python environment in hw5/.venv. Also support the normal sibling layout.
CANDIDATES = [ROOT / "HW5" / "hw5", ROOT / "hw5"]
HW5_SOURCE = next((p for p in CANDIDATES if (p / "setup_schema.py").is_file()), None)
if HW5_SOURCE is None:
    raise SystemExit(
        "Could not find setup_schema.py. Checked: "
        + ", ".join(str(x) for x in CANDIDATES)
    )
sys.path.insert(0, str(HW5_SOURCE))

from setup_schema import (  # noqa: E402
    DB_NAME,
    DB_USER,
    INSTANCE_CONNECTION_NAME,
    get_credentials,
)
from google.cloud.sql.connector import Connector, IPTypes  # noqa: E402

SQL_FILE = Path(__file__).with_name("step2_3nf.sql")


def scalar(cur, sql: str) -> int:
    cur.execute(sql)
    return int(cur.fetchone()[0])


def preflight(cur) -> int:
    print("Preflight: checking original HW5 data, without modifying it...", flush=True)
    source_count = scalar(cur, "SELECT COUNT(*) FROM requests")
    missing_ips = scalar(cur, "SELECT COUNT(*) FROM requests WHERE client_ip IS NULL")
    if missing_ips:
        raise ValueError(f"Found {missing_ips} missing client IPs; manual strategy needed")

    cur.execute("""
        SELECT country, COUNT(DISTINCT is_banned) AS values_seen
        FROM requests
        WHERE country IS NOT NULL
        GROUP BY country
        HAVING COUNT(DISTINCT is_banned) > 1
        ORDER BY country
        LIMIT 5
    """)
    conflicts = cur.fetchall()
    if conflicts:
        raise ValueError(f"Country->is_banned dependency violated: {conflicts}")
    missing_country_banned = scalar(
        cur,
        "SELECT COUNT(*) FROM requests WHERE country IS NULL AND is_banned IS TRUE",
    )
    if missing_country_banned:
        raise ValueError(
            f"Found {missing_country_banned} rows with no country but is_banned=TRUE"
        )
    print(f"  Original requests: {source_count:,}", flush=True)
    return source_count


def verify(cur, source_count: int) -> dict:
    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM hw6_requests) AS migrated_requests,
            (SELECT COUNT(*) FROM hw6_ip_country) AS unique_ips,
            (SELECT COUNT(*) FROM hw6_countries) AS countries,
            (SELECT COUNT(*) FROM hw6_request_country_exceptions) AS country_exceptions,
            (SELECT COUNT(*) FROM failed_requests) AS original_failures
    """)
    counts = cur.fetchone()
    labels = ["migrated_requests", "unique_ips", "countries", "country_exceptions", "original_failures"]
    results = dict(zip(labels, (int(x) for x in counts)))
    results["original_requests"] = source_count
    if results["migrated_requests"] != source_count:
        raise ValueError(f"Mismatched row count: {results}")

    # Check all retained request-specific fields, including the original timestamps.
    field_mismatches = scalar(cur, """
        SELECT COUNT(*)
        FROM requests AS old
        JOIN hw6_requests AS new USING (request_id)
        WHERE ROW(
            old.client_ip, old.gender, old.age, old.income, old.time_of_day,
            old.requested_file, old.status_code, old.received_at
        ) IS DISTINCT FROM ROW(
            new.client_ip, new.gender, new.age, new.income, new.time_of_day,
            new.requested_file, new.status_code, new.received_at
        )
    """)
    if field_mismatches:
        raise ValueError(f"Found {field_mismatches} altered request field rows")

    # Verify that mapping + sparse exceptions perfectly reconstruct ORIGINAL
    # country AND banned flag, including NULL values.
    reconstruction_mismatches = scalar(cur, """
        WITH restored AS (
            SELECT
                facts.request_id,
                CASE WHEN exception.request_id IS NOT NULL
                    THEN original.country
                    ELSE mapped.country
                END AS original_country,
                CASE WHEN exception.request_id IS NOT NULL
                    THEN COALESCE(original.is_banned, FALSE)
                    ELSE COALESCE(mapped.is_banned, FALSE)
                END AS original_banned
            FROM hw6_requests AS facts
            JOIN hw6_ip_country AS ip USING (client_ip)
            LEFT JOIN hw6_request_country_exceptions AS exception USING (request_id)
            LEFT JOIN hw6_countries AS original
                ON original.country_id = exception.original_country_id
            LEFT JOIN hw6_countries AS mapped
                ON mapped.country_id = ip.country_id
        )
        SELECT COUNT(*)
        FROM requests AS raw
        JOIN restored ON restored.request_id = raw.request_id
        WHERE raw.country IS DISTINCT FROM restored.original_country
           OR raw.is_banned IS DISTINCT FROM restored.original_banned
    """)
    if reconstruction_mismatches:
        raise ValueError(
            f"Could not reconstruct country/ban of {reconstruction_mismatches} records"
        )
    results["field_mismatches"] = field_mismatches
    results["country_reconstruction_mismatches"] = reconstruction_mismatches

    cur.execute("""
        SELECT client_ip::text, COUNT(DISTINCT country) AS countries
        FROM requests
        WHERE country IS NOT NULL
        GROUP BY client_ip
        HAVING COUNT(DISTINCT country) > 1
        ORDER BY client_ip::text
        LIMIT 10
    """)
    results["example_conflicting_ips"] = [
        {"client_ip": ip, "country_count": int(cnt)} for ip, cnt in cur.fetchall()
    ]
    return results


def main() -> None:
    password = os.environ.get("HW6_DB_PASSWORD")
    if not password:
        raise SystemExit("Missing HW6_DB_PASSWORD (the wrapper loads it securely).")
    statements = []
    for chunk in re.split(r"(?m)^-- HW6-SQL-SPLIT[ \t]*$", SQL_FILE.read_text()):
        # A trailing documentation-only section is not a SQL statement.
        uncommented = re.sub(r"(?m)^[ \t]*--[^\n]*$", "", chunk).strip()
        if uncommented:
            statements.append(chunk.strip())
    print(f"Cloud SQL: {INSTANCE_CONNECTION_NAME} / {DB_NAME}", flush=True)
    print(f"HW5 code: {HW5_SOURCE}", flush=True)
    print("Creating a separate 3NF copy; original HW5 tables remain unchanged.", flush=True)

    with Connector(credentials=get_credentials(), refresh_strategy="LAZY") as connector:
        db = connector.connect(
            INSTANCE_CONNECTION_NAME, "pg8000",
            db=DB_NAME, user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
        )
        try:
            cur = db.cursor()
            try:
                cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                source_count = preflight(cur)
                for index, statement in enumerate(statements, 1):
                    print(f"Executing migration SQL statement {index}/{len(statements)}...", flush=True)
                    cur.execute(statement)
                print("Verifying normalized data before committing...", flush=True)
                report = verify(cur, source_count)
                db.commit()
                print("Migration COMMITTED successfully.", flush=True)
                print(json.dumps(report, indent=2, sort_keys=True), flush=True)
            except BaseException:
                db.rollback()
                print("Migration ROLLED BACK: HW5 data untouched.", file=sys.stderr, flush=True)
                raise
            finally:
                cur.close()
        finally:
            db.close()


if __name__ == "__main__":
    main()

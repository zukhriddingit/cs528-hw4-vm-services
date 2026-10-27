#!/usr/bin/env python3
"""Read-only HW5 database snapshots for curl before/after evidence."""

import argparse
import json
from pathlib import Path

from google.cloud.sql.connector import Connector, IPTypes
from setup_schema import DB_NAME, DB_USER, INSTANCE_CONNECTION_NAME, get_credentials


def print_table(cursor, table, columns, limit):
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    count = cursor.fetchone()[0]
    print(f"\n{table}: {count} total rows", flush=True)
    cols = ", ".join(columns)
    primary = "request_id" if table == "requests" else "failure_id"
    cursor.execute(f"SELECT {cols} FROM {table} ORDER BY {primary} DESC LIMIT %s", (limit,))
    rows = cursor.fetchall()
    if not rows:
        print("  (empty)")
    else:
        for row in reversed(rows):
            print("  " + json.dumps(dict(zip(columns, row)), default=str, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="Show HW5 request and failure tables without modifying data")
    parser.add_argument("--limit", type=int, default=3, help="recent rows per table")
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error("--limit must be between 1 and 100")
    password_file = Path(__file__).with_name(".db_password")
    if not password_file.is_file():
        parser.error(f"Missing local password file: {password_file}")
    password = password_file.read_text(encoding="utf-8").strip()
    if not password:
        parser.error("Local password file is empty")
    print(f"Cloud SQL: {INSTANCE_CONNECTION_NAME} / {DB_NAME}")
    with Connector(credentials=get_credentials(), refresh_strategy="LAZY") as connector:
        conn = connector.connect(
            INSTANCE_CONNECTION_NAME, "pg8000", db=DB_NAME,
            user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
        )
        try:
            cur = conn.cursor()
            try:
                print_table(cur, "requests", [
                    "request_id", "received_at", "requested_file", "status_code", "country",
                    "client_ip", "gender", "age", "income", "is_banned", "time_of_day",
                ], args.limit)
                print_table(cur, "failed_requests", [
                    "failure_id", "request_time", "requested_file", "error_code",
                ], args.limit)
            finally:
                cur.close()
        finally:
            conn.close()


if __name__ == "__main__":
    main()

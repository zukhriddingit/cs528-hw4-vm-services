#!/usr/bin/env python3
"""Non-mutating database schema check for setup.sh (exit 0 iff both tables exist)."""
import os
import sys

from google.cloud.sql.connector import Connector, IPTypes
from setup_schema import (
    DB_NAME, DB_USER, INSTANCE_CONNECTION_NAME, get_credentials,
)


def main():
    password = os.environ.get("DB_PASS")
    if not password:
        raise SystemExit("DB_PASS is required")
    with Connector(credentials=get_credentials(), refresh_strategy="LAZY") as connector:
        connection = connector.connect(
            INSTANCE_CONNECTION_NAME, "pg8000", db=DB_NAME,
            user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
        )
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' "
                    "AND table_name IN ('requests', 'failed_requests')"
                )
                found = {row[0] for row in cursor.fetchall()}
            finally:
                cursor.close()
        finally:
            connection.close()
    if found != {"requests", "failed_requests"}:
        print(f"Incomplete schema: {found}", file=sys.stderr)
        return 1
    print("Schema exists: requests, failed_requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

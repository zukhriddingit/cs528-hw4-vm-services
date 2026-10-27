#!/usr/bin/env python3
"""Idempotently create the HW5 database and its two tables.

Requires an existing running Cloud SQL instance and a built-in PostgreSQL user
with CREATEDB permission. The accompanying initialize_database.sh handles both.
"""

import os
import re
import subprocess
from pathlib import Path

import google.auth
from google.auth.exceptions import DefaultCredentialsError
from google.cloud.sql.connector import Connector, IPTypes
from google.oauth2.credentials import Credentials

PROJECT_ID = "quick-formula-457319-q3"
INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME", f"{PROJECT_ID}:us-central1:hw5-sql"
)
DB_NAME = os.environ.get("DB_NAME", "hw5_requests")
DB_USER = os.environ.get("DB_USER", "hw5_user")


def get_credentials():
    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return credentials
    except DefaultCredentialsError:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token", f"--project={PROJECT_ID}"],
            text=True,
        ).strip()
        return Credentials(token=token)


def initialize_database(connector, password):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", DB_NAME):
        raise ValueError("DB_NAME must be a lowercase PostgreSQL identifier")

    connection = connector.connect(
        INSTANCE_CONNECTION_NAME, "pg8000", db="postgres",
        user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
    )
    try:
        connection.autocommit = True  # PostgreSQL CREATE DATABASE cannot run in a transaction.
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
            if cursor.fetchone() is None:
                cursor.execute(f'CREATE DATABASE "{DB_NAME}" OWNER "{DB_USER}"')
                print(f"Created PostgreSQL database: {DB_NAME}")
            else:
                print(f"PostgreSQL database already exists: {DB_NAME}")
        finally:
            cursor.close()
    finally:
        connection.close()


def initialize_tables(connector, password):
    sql = (Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    connection = connector.connect(
        INSTANCE_CONNECTION_NAME, "pg8000", db=DB_NAME,
        user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
    )
    try:
        cursor = connection.cursor()
        try:
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name IN ('requests','failed_requests') "
                "ORDER BY table_name"
            )
            tables = [row[0] for row in cursor.fetchall()]
            if tables != ["failed_requests", "requests"]:
                raise RuntimeError(f"Missing tables after setup: {tables}")
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
    finally:
        connection.close()


def main():
    password = os.environ.get("DB_PASS")
    if not password:
        raise SystemExit("DB_PASS must be set before running setup_schema.py")
    credentials = get_credentials()
    with Connector(credentials=credentials, refresh_strategy="LAZY") as connector:
        initialize_database(connector, password)
        initialize_tables(connector, password)
    print(f"Schema ready: {INSTANCE_CONNECTION_NAME}/{DB_NAME} (requests, failed_requests)")


if __name__ == "__main__":
    main()

"""CS 528 HW5: GCS file server with Cloud SQL request logging and timings.

Builds on HW4's Flask app; HW4 service2 remains unchanged.
"""

import csv
import ipaddress
import json
import os
import re
import threading
import time
from datetime import datetime, time as clock_time, timezone
from pathlib import Path

from flask import Flask, Response, request
from google.api_core.exceptions import NotFound
from google.cloud import logging as cloud_logging
from google.cloud import pubsub_v1, storage
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import create_engine, text

app = Flask(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "quick-formula-457319-q3")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "cloud_hw2_bucket")
TOPIC_ID = os.environ.get("TOPIC_ID", "hw3-forbidden-requests")
INSTANCE_CONNECTION_NAME = os.environ.get(
    "INSTANCE_CONNECTION_NAME", f"{PROJECT_ID}:us-central1:hw5-sql"
)
DB_NAME = os.environ.get("DB_NAME", "hw5_requests")
DB_USER = os.environ.get("DB_USER", "hw5_user")
METRICS_FILE = os.environ.get("HW5_METRICS_FILE", "/tmp/hw5_timings.csv")

FORBIDDEN_COUNTRIES = {
    "north korea", "iran", "cuba", "myanmar", "iraq",
    "libya", "sudan", "zimbabwe", "syria",
}
SUPPORTED_METHODS = [
    "GET", "POST", "PUT", "DELETE", "HEAD", "CONNECT", "OPTIONS", "TRACE", "PATCH"
]

_storage_client = None
_publisher = None
_log_writer = None
_connector = None
_db_engine = None
_db_lock = threading.Lock()
_metrics_lock = threading.Lock()

METRIC_COLUMNS = [
    "recorded_at_utc", "http_method", "requested_file", "status_code",
    "header_ms", "storage_ms", "database_ms", "response_prepare_ms",
    "response_send_ms", "total_ms", "response_bytes",
]


def storage_client():
    global _storage_client
    if _storage_client is None:
        _storage_client = storage.Client(project=PROJECT_ID)
    return _storage_client


def publisher():
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def log_writer():
    global _log_writer
    if _log_writer is None:
        _log_writer = cloud_logging.Client(project=PROJECT_ID).logger("hw4-web")
    return _log_writer


def log_event(severity, kind, message, **details):
    payload = {
        "event_type": kind,
        "message": message,
        "http_method": request.method,
        "request_path": request.path,
        **details,
    }
    try:
        log_writer().log_struct(payload, severity=severity)
    except Exception:
        app.logger.exception("Could not send structured log to Cloud Logging")


def _db_password():
    # The final VM startup script will retrieve this secret; never commit it.
    file_name = os.environ.get("DB_PASS_FILE")
    password = Path(file_name).read_text(encoding="utf-8").strip() if file_name else os.environ.get("DB_PASS", "")
    if not password:
        raise RuntimeError("Set DB_PASS_FILE or DB_PASS to the PostgreSQL password")
    return password


def database_engine():
    """One thread-safe SQLAlchemy pool per Gunicorn worker."""
    global _connector, _db_engine
    if _db_engine is None:
        with _db_lock:
            if _db_engine is None:
                _connector = Connector(refresh_strategy="LAZY")

                def connect_to_sql():
                    return _connector.connect(
                        INSTANCE_CONNECTION_NAME, "pg8000", db=DB_NAME,
                        user=DB_USER, password=_db_password(),
                        ip_type=IPTypes.PUBLIC,
                    )

                _db_engine = create_engine(
                    "postgresql+pg8000://", creator=connect_to_sql,
                    pool_size=5, max_overflow=3, pool_timeout=30,
                    pool_recycle=1800, pool_pre_ping=True,
                )
    return _db_engine


def parse_time_header(raw):
    """Interpret X-time as an ISO datetime, Unix epoch, HH:MM[:SS], or 0-23 hour.

    Keep TIMESTAMP WITHOUT TIME ZONE values in UTC. Missing/invalid headers
    remain NULL; received_at records the real request receipt time.
    """
    if not raw:
        return None
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?", value):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        if re.fullmatch(r"\d{1,2}", value) and 0 <= int(value) <= 23:
            today = datetime.now(timezone.utc).date()
            return datetime.combine(today, clock_time(hour=int(value)))
        if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", value):
            today = datetime.now(timezone.utc).date()
            return datetime.combine(today, clock_time.fromisoformat(value))
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is not None:
            result = result.astimezone(timezone.utc).replace(tzinfo=None)
        return result
    except (OverflowError, ValueError):
        return None


def parse_client_ip(raw):
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def extract_request_information(filename):
    """All request headers and POST filename extraction happen here."""
    start = time.perf_counter_ns()
    headers = request.headers
    country = (headers.get("X-country") or "").strip()
    provided_ip = headers.get("X-client-IP") or request.remote_addr
    parsed_filename = filename
    malformed_post = False
    if request.method == "POST":
        payload = request.get_json(silent=True)
        if isinstance(payload, dict) and isinstance(payload.get("file"), str):
            parsed_filename = payload["file"]
        else:
            malformed_post = True

    info = {
        "country": country or None,
        "client_ip": parse_client_ip(provided_ip),
        "gender": (headers.get("X-gender") or "").strip() or None,
        "age": (headers.get("X-age") or "").strip() or None,
        "income": (headers.get("X-income") or "").strip() or None,
        "is_banned": country.lower() in FORBIDDEN_COUNTRIES,
        "time_of_day": parse_time_header(headers.get("X-time")),
        "requested_file": parsed_filename,
        "malformed_post": malformed_post,
    }
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    return info, elapsed_ms


def read_serving_file(filename, timings):
    """Fetch a GCS object, measuring even failed fetches."""
    start = time.perf_counter_ns()
    try:
        if not isinstance(filename, str) or not re.fullmatch(r"\d+\.html", filename):
            return None
        try:
            return storage_client().bucket(BUCKET_NAME).blob(
                f"pages/{filename}"
            ).download_as_bytes()
        except NotFound:
            return None
    finally:
        timings["storage_ms"] = (time.perf_counter_ns() - start) / 1e6


def send_forbidden_message(country):
    payload = {
        "country": country,
        "method": request.method,
        "path": request.path,
        "message": f"Permission denied for requests from {country}",
    }
    data = json.dumps(payload).encode("utf-8")
    publisher().publish(
        publisher().topic_path(PROJECT_ID, TOPIC_ID), data=data
    ).result(timeout=10)


def insert_request_into_database(info, status_code):
    """Atomically store every request and (if non-200) a separate error row."""
    params = {k: info[k] for k in (
        "country", "client_ip", "gender", "age", "income", "is_banned",
        "time_of_day", "requested_file"
    )}
    params["status_code"] = status_code

    with database_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO requests (
                country, client_ip, gender, age, income, is_banned,
                time_of_day, requested_file, status_code
            ) VALUES (
                :country, CAST(:client_ip AS INET), :gender, :age, :income,
                :is_banned, :time_of_day, :requested_file, :status_code
            )
        """), params)
        if status_code != 200:
            conn.execute(text("""
                INSERT INTO failed_requests (request_time, requested_file, error_code)
                VALUES (CURRENT_TIMESTAMP, :requested_file, :error_code)
            """), {
                "requested_file": info["requested_file"],
                "error_code": status_code,
            })


def construct_response(body, status_code, mimetype):
    """Measure Flask response preparation; actual WSGI write is timed below."""
    start = time.perf_counter_ns()
    result = Response(body, status=status_code, mimetype=mimetype)
    return result, (time.perf_counter_ns() - start) / 1e6


def record_timing(metrics):
    """One row per request. Gunicorn should run one worker with threads."""
    try:
        with _metrics_lock:
            path = Path(METRICS_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            is_new = not path.exists() or path.stat().st_size == 0
            with path.open("a", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=METRIC_COLUMNS)
                if is_new:
                    writer.writeheader()
                writer.writerow({column: metrics.get(column) for column in METRIC_COLUMNS})
    except Exception:
        app.logger.exception("Could not save HW5 timing record")


class TimedWSGIResponse:
    """Measure the time the WSGI server spends consuming response chunks.

    This includes the Gunicorn write() interval but is NOT the total client
    download time or a guarantee the remote peer acknowledged the response.
    """
    def __init__(self, wrapped_app):
        self.wrapped_app = wrapped_app

    def __call__(self, environ, start_response):
        overall_start = time.perf_counter_ns()
        result = self.wrapped_app(environ, start_response)

        def stream():
            send_ns = 0
            payload_size = 0
            try:
                for chunk in result:
                    payload_size += len(chunk)
                    start = time.perf_counter_ns()
                    yield chunk
                    send_ns += time.perf_counter_ns() - start
            finally:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                metrics = environ.get("hw5.metrics")
                if metrics is not None:
                    metrics["response_send_ms"] = round(send_ns / 1e6, 4)
                    metrics["total_ms"] = round((time.perf_counter_ns() - overall_start) / 1e6, 4)
                    metrics["response_bytes"] = payload_size
                    record_timing(metrics)

        return stream()


@app.route("/", defaults={"filename": ""}, methods=SUPPORTED_METHODS,
           provide_automatic_options=False)
@app.route("/<path:filename>", methods=SUPPORTED_METHODS,
           provide_automatic_options=False)
def handle_request(filename):
    info, header_ms = extract_request_information(filename)
    country = info["country"] or ""
    timings = {"storage_ms": 0.0}

    if info["is_banned"]:
        status_code = 400
        message = f"Permission denied for requests from {country}"
        log_event("CRITICAL", "FORBIDDEN_COUNTRY", message, country=country)
        try:
            send_forbidden_message(country)
        except Exception:
            app.logger.exception("Unable to publish forbidden-country event")
        body, mimetype = message, "text/plain"
    elif request.method not in ("GET", "POST"):
        status_code = 501
        message = f"HTTP method {request.method} is not implemented"
        log_event("ERROR", "501_NOT_IMPLEMENTED", message)
        body, mimetype = message, "text/plain"
    elif info["malformed_post"]:
        status_code = 400
        body, mimetype = "POST body must contain a 'file' string", "text/plain"
    else:
        try:
            contents = read_serving_file(info["requested_file"], timings)
        except Exception:
            app.logger.exception("Storage lookup failed")
            status_code = 503
            body, mimetype = "Storage temporarily unavailable", "text/plain"
        else:
            if contents is None:
                status_code = 404
                body = f"File {info['requested_file']} does not exist"
                mimetype = "text/plain"
                log_event("WARNING", "404_NOT_FOUND", body,
                          filename=info["requested_file"])
            else:
                status_code = 200
                body, mimetype = contents, "text/html"

    response, response_prepare_ms = construct_response(body, status_code, mimetype)
    database_start = time.perf_counter_ns()
    try:
        insert_request_into_database(info, status_code)
    except Exception:
        app.logger.exception("Database insertion failed")
        # Report the failure rather than falsely returning success without a row.
        status_code = 503
        response, extra_ms = construct_response(
            "Database temporarily unavailable", 503, "text/plain"
        )
        response_prepare_ms += extra_ms
    database_ms = (time.perf_counter_ns() - database_start) / 1e6

    request.environ["hw5.metrics"] = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "http_method": request.method,
        "requested_file": info["requested_file"],
        "status_code": status_code,
        "header_ms": round(header_ms, 4),
        "storage_ms": round(timings["storage_ms"], 4),
        "database_ms": round(database_ms, 4),
        "response_prepare_ms": round(response_prepare_ms, 4),
    }
    return response


# Instrument the WSGI send phase, which occurs after Flask's route returns.
app.wsgi_app = TimedWSGIResponse(app.wsgi_app)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)

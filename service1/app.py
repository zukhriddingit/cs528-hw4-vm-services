"""HW4 file-serving VM app (adapted from HW3 service1)."""

import json
import os
import re

from flask import Flask, Response, request
from google.api_core.exceptions import NotFound
from google.cloud import logging as cloud_logging
from google.cloud import pubsub_v1, storage

app = Flask(__name__)
PROJECT_ID = os.environ.get("PROJECT_ID", "quick-formula-457319-q3")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "cloud_hw2_bucket")
TOPIC_ID = os.environ.get("TOPIC_ID", "hw3-forbidden-requests")

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
        "status_category": kind,
        **details,
    }
    try:
        log_writer().log_struct(payload, severity=severity)
    except Exception:
        app.logger.exception("Could not send structured log to Cloud Logging")


def get_file(filename):
    # HW2 pages are numbered, so avoid fetching arbitrary object names.
    if not isinstance(filename, str) or not re.fullmatch(r"\d+\.html", filename):
        return None
    try:
        return storage_client().bucket(BUCKET_NAME).blob(f"pages/{filename}").download_as_bytes()
    except NotFound:
        return None


def send_forbidden_message(country):
    payload = {
        "country": country,
        "method": request.method,
        "path": request.path,
        "message": f"Permission denied for requests from {country}",
    }
    data = json.dumps(payload).encode("utf-8")
    topic_path = publisher().topic_path(PROJECT_ID, TOPIC_ID)
    publisher().publish(topic_path, data=data).result(timeout=10)


@app.route("/", defaults={"filename": ""}, methods=SUPPORTED_METHODS, provide_automatic_options=False)
@app.route("/<path:filename>", methods=SUPPORTED_METHODS, provide_automatic_options=False)
def handle_request(filename):
    country = request.headers.get("X-country", "").strip()
    if country.lower() in FORBIDDEN_COUNTRIES:
        message = f"Permission denied for requests from {country}"
        log_event("CRITICAL", "FORBIDDEN_COUNTRY", message, country=country)
        try:
            send_forbidden_message(country)
        except Exception:
            app.logger.exception("Unable to publish forbidden-country event")
        return Response(message, status=400, mimetype="text/plain")

    if request.method not in ("GET", "POST"):
        message = f"HTTP method {request.method} is not implemented"
        log_event("ERROR", "501_NOT_IMPLEMENTED", message)
        return Response(message, status=501, mimetype="text/plain")

    if request.method == "POST":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("file"), str):
            return Response("POST body must contain a 'file' string", status=400, mimetype="text/plain")
        filename = payload["file"]

    try:
        contents = get_file(filename)
    except Exception:
        app.logger.exception("Storage lookup failed")
        return Response("Storage temporarily unavailable", status=503, mimetype="text/plain")

    if contents is None:
        message = f"File {filename} does not exist"
        log_event("WARNING", "404_NOT_FOUND", message, filename=filename)
        return Response(message, status=404, mimetype="text/plain")

    return Response(contents, status=200, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)

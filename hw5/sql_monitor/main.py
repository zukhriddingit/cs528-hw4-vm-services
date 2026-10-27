"""Hourly HW5 Cloud SQL safety-stop function (authenticated HTTP endpoint)."""
import json
import logging

import google.auth
from google.auth.transport.requests import AuthorizedSession

PROJECT_ID = "quick-formula-457319-q3"
INSTANCE = "hw5-sql"
ENDPOINT = (
    f"https://sqladmin.googleapis.com/sql/v1beta4/projects/"
    f"{PROJECT_ID}/instances/{INSTANCE}"
)


def stop_cloud_sql(request):
    """Check current instance state and request stop if it is running."""
    if request.method != "POST":
        return ("POST required", 405)

    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    current = session.get(ENDPOINT, timeout=30)
    if current.status_code == 404:
        return ("Instance does not exist; nothing to stop", 200)
    current.raise_for_status()
    instance = current.json()
    state = instance.get("state")
    policy = (instance.get("settings") or {}).get("activationPolicy")
    if state != "RUNNABLE" or policy == "NEVER":
        message = f"No action: state={state}, activationPolicy={policy}"
        logging.info(message)
        return (message, 200)

    response = session.patch(
        ENDPOINT, json={"settings": {"activationPolicy": "NEVER"}}, timeout=30
    )
    response.raise_for_status()
    operation = response.json()
    logging.info("Requested Cloud SQL stop: %s", json.dumps(operation))
    return (f"Stop requested; operation={operation.get('name', 'unknown')}", 200)

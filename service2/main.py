"""HW4 forbidden-country reporter (Pub/Sub worker on VM 3)."""

import json
import os
from datetime import datetime, timezone

from google.cloud import pubsub_v1

PROJECT_ID = os.environ.get("PROJECT_ID", "quick-formula-457319-q3")
SUBSCRIPTION_ID = os.environ.get("SUBSCRIPTION_ID", "hw4-forbidden-sub")


def callback(message):
    try:
        data = json.loads(message.data.decode("utf-8"))
        timestamp = datetime.now(timezone.utc).isoformat()
        print(
            f"{timestamp} - Permission denied: forbidden request from "
            f"{data.get('country', 'Unknown')} method={data.get('method', 'Unknown')} "
            f"path={data.get('path', 'Unknown')}",
            flush=True,
        )
        message.ack()
    except Exception as exc:
        print(f"Error processing forbidden event: {exc}", flush=True)
        message.nack()


def main():
    subscriber = pubsub_v1.SubscriberClient()
    subscription = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)
    print(f"Listening on {subscription}", flush=True)
    future = subscriber.subscribe(subscription, callback=callback)
    try:
        future.result()
    except KeyboardInterrupt:
        future.cancel()


if __name__ == "__main__":
    main()

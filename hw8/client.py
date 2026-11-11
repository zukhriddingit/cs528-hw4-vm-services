#!/usr/bin/env python3
"""HW8 monitoring client: one request per second, per-zone CSV evidence."""

import argparse
import csv
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main():
    p = argparse.ArgumentParser(description="CS528 HW8 zone-aware HTTP client")
    p.add_argument("--url", default=os.getenv("HW8_URL", "http://34.63.229.117:8080/0.html"))
    p.add_argument("--count", type=int, default=90)
    p.add_argument("--interval", type=float, default=1.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--method", default="GET")
    p.add_argument("--country", default=None)
    p.add_argument("--file", default="0.html")
    p.add_argument("--out", default="results/requests.csv")
    args = p.parse_args()
    if args.count < 1 or args.interval <= 0 or args.timeout <= 0:
        p.error("--count must be positive; --interval and --timeout must be > 0")

    method = args.method.upper()
    headers = {"Connection": "close"}
    if args.country:
        headers["X-country"] = args.country
    body = None
    if method == "POST":
        body = json.dumps({"file": args.file}).encode("utf-8")
        headers["Content-Type"] = "application/json"

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    counts = Counter()
    start = time.monotonic()

    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "elapsed_s", "status", "zone", "latency_ms", "error"])
        try:
            for i in range(args.count):
                time.sleep(max(0.0, start + i * args.interval - time.monotonic()))
                timestamp = utc_now()
                elapsed = time.monotonic() - start
                t0 = time.monotonic()
                status, zone, error = None, "", ""
                request = Request(args.url, data=body, headers=headers, method=method)
                try:
                    with urlopen(request, timeout=args.timeout) as response:
                        status = response.status
                        zone = response.headers.get("X-Server-Zone", "")
                        response.read()
                except HTTPError as exc:
                    status = exc.code
                    zone = exc.headers.get("X-Server-Zone", "")
                    error = f"HTTP {status}"
                except Exception as exc:
                    status, zone = None, ""
                    error = f"{type(exc).__name__}: {exc}"
                latency_ms = round((time.monotonic() - t0) * 1000, 1)
                key = zone if status == 200 and zone and not error else "ERROR"
                counts[key] += 1
                writer.writerow([timestamp, round(elapsed, 3), status or "", zone, latency_ms, error])
                handle.flush()
                print(
                    f"{timestamp} | {status or 'ERR'} | {zone or '-'} | "
                    f"{latency_ms}ms | {error}",
                    flush=True,
                )
        except KeyboardInterrupt:
            print("\nStopped by user.", flush=True)

    print("\nRequest summary:")
    for name, count in sorted(counts.items()):
        print(f"{name}: {count}")
    print(f"Results saved to {args.out}")


if __name__ == "__main__":
    main()

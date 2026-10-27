#!/usr/bin/env python3
"""Summarize request timing measurements emitted by HW5 Flask application."""

import argparse
import collections
import csv
import statistics
from pathlib import Path

COLUMNS = [
    ("header_ms", "Header extraction"),
    ("storage_ms", "Cloud Storage read"),
    ("database_ms", "Cloud SQL insert/transaction"),
    ("response_prepare_ms", "Response object preparation"),
    ("response_send_ms", "WSGI response-send interval"),
    ("total_ms", "Total server processing"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    rows = collections.Counter()
    values = {column: [] for column, _ in COLUMNS}
    with args.csv_path.open(newline="", encoding="utf-8") as input_file:
        for record in csv.DictReader(input_file):
            rows[record.get("status_code", "?")] += 1
            for column, _ in COLUMNS:
                if record.get(column):
                    values[column].append(float(record[column]))
    print(f"Requests measured: {sum(rows.values()):,}")
    print(f"Status codes: {dict(rows)}")
    print(f"{'Phase':36s} {'Mean ms':>12s} {'Median ms':>12s} {'P95 ms':>12s}")
    for column, title in COLUMNS:
        v = values[column]
        if not v:
            continue
        v.sort()
        p95 = v[max(0, int((len(v) - 1) * .95))]
        print(f"{title:36s} {statistics.fmean(v):12.4f} "
              f"{statistics.median(v):12.4f} {p95:12.4f}")
    print("Note: WSGI send interval approximates server-side writes, not end-to-end client transfer time.")


if __name__ == "__main__":
    main()

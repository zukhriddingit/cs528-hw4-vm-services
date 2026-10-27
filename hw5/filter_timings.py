#!/usr/bin/env python3
"""Extract just one HW5 load-test time window from server-side timing CSV."""

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path


def parse_timestamp(value):
    parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('UTC timestamp must include Z or an offset')
    return parsed.astimezone(timezone.utc)


def filter_csv(input_path, output_path, start_utc, end_utc):
    start, end = parse_timestamp(start_utc), parse_timestamp(end_utc)
    if start > end:
        raise ValueError('Start UTC must be before end UTC')
    result_count = 0
    with input_path.open('r', newline='', encoding='utf-8') as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or 'recorded_at_utc' not in reader.fieldnames:
            raise ValueError('Not an HW5 timing CSV (missing recorded_at_utc)')
        with output_path.open('w', newline='', encoding='utf-8') as target:
            writer = csv.DictWriter(target, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                stamp = row.get('recorded_at_utc')
                if stamp and start <= parse_timestamp(stamp) <= end:
                    writer.writerow(row)
                    result_count += 1
    return result_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_csv', type=Path)
    parser.add_argument('output_csv', type=Path)
    parser.add_argument('--start-utc', required=True)
    parser.add_argument('--end-utc', required=True)
    args = parser.parse_args()
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    count = filter_csv(args.source_csv, args.output_csv, args.start_utc, args.end_utc)
    print(f'Wrote {count:,} timing rows for load-test window to {args.output_csv}')


if __name__ == '__main__':
    main()

"""Measure 1 Hz VM CPU, system memory and network bytes during a stress run."""

import argparse
import csv
import time
from datetime import datetime, timezone

import psutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    prev_net = psutil.net_io_counters()
    prev_time = time.monotonic()
    psutil.cpu_percent(interval=None)

    with open(args.output, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "timestamp_utc", "elapsed_s", "cpu_pct", "memory_pct", "memory_used_mb",
            "rx_mbps", "tx_mbps", "total_mbps",
        ])
        writer.writeheader()
        started = time.monotonic()
        while time.monotonic() - started < args.duration:
            time.sleep(1)
            now = time.monotonic()
            net = psutil.net_io_counters()
            period = max(now - prev_time, 1e-6)
            rx_mbps = max(net.bytes_recv - prev_net.bytes_recv, 0) * 8 / period / 1e6
            tx_mbps = max(net.bytes_sent - prev_net.bytes_sent, 0) * 8 / period / 1e6
            mem = psutil.virtual_memory()
            writer.writerow({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(now - started, 2),
                "cpu_pct": psutil.cpu_percent(interval=None),
                "memory_pct": mem.percent,
                "memory_used_mb": round(mem.used / 1e6, 2),
                "rx_mbps": round(rx_mbps, 5),
                "tx_mbps": round(tx_mbps, 5),
                "total_mbps": round(rx_mbps + tx_mbps, 5),
            })
            handle.flush()
            prev_net = net
            prev_time = now
    print(f"Metrics saved to {args.output}")


if __name__ == "__main__":
    main()

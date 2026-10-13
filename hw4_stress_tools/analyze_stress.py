#!/usr/bin/env python3
"""Summarize server metrics from HW4 stress runs and draw dependency-free SVG graphs."""

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import mean
from xml.sax.saxutils import escape


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def summarize_one(n, directory):
    summary_path = directory / f"summary_{n}.json"
    csv_path = directory / f"metrics_{n}.csv"
    if not summary_path.exists() or not csv_path.exists():
        return None
    run = json.loads(summary_path.read_text())
    start, end = parse_time(run["start_utc"]), parse_time(run["end_utc"])
    with csv_path.open(newline="") as f:
        source = list(csv.DictReader(f))
    rows = [r for r in source if start <= parse_time(r["timestamp_utc"]) <= end]
    if not rows:
        raise RuntimeError(f"No metrics overlapped stress interval for {n} clients; check VM clocks")

    def stats(field):
        values = [float(r[field]) for r in rows]
        return round(mean(values), 3), round(max(values), 3)

    cpu_avg, cpu_peak = stats("cpu_pct")
    mem_avg, mem_peak = stats("memory_used_mb")
    net_avg, net_peak = stats("total_mbps")
    rx_avg, rx_peak = stats("rx_mbps")
    tx_avg, tx_peak = stats("tx_mbps")
    return {
        "clients": n,
        "samples": len(rows),
        "measurement_start_utc": run["start_utc"],
        "measurement_end_utc": run["end_utc"],
        "avg_cpu_pct": cpu_avg,
        "peak_cpu_pct": cpu_peak,
        "avg_memory_used_mb": mem_avg,
        "peak_memory_used_mb": mem_peak,
        "avg_bandwidth_mbps": net_avg,
        "peak_bandwidth_mbps": net_peak,
        "avg_ingress_mbps": rx_avg,
        "peak_ingress_mbps": rx_peak,
        "avg_egress_mbps": tx_avg,
        "peak_egress_mbps": tx_peak,
        "http_200": run["successful_200"],
        "expected_http_400": run["expected_denied_400"],
        "other_http_statuses": run["other_http_responses"],
        "client_process_failures": run["client_process_failures"],
        "total_http_responses": run["total_http_responses"],
    }


def bar_chart(rows, mean_key, peak_key, title, units, output):
    # No external visualization library required on the student's Mac.
    width, height = 1000, 620
    x0, x1, top, bottom = 130, 940, 90, 495
    max_v = max((float(r[peak_key]) for r in rows), default=0)
    axis_top = max(1.0, max_v * 1.16)
    available = x1 - x0
    pieces = []

    def line(xa, ya, xb, yb, color="#e0e6ed", stroke=1):
        pieces.append(f'<line x1="{xa:.1f}" y1="{ya:.1f}" x2="{xb:.1f}" y2="{yb:.1f}" stroke="{color}" stroke-width="{stroke}"/>')

    def text(x, y, s, *, size=15, color="#202a35", anchor="middle", weight="normal"):
        pieces.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}" font-family="Arial, sans-serif">{escape(str(s))}</text>')

    pieces.append('<rect x="0" y="0" width="1000" height="620" fill="white"/>')
    text(500, 42, title, size=26, weight="bold")
    text(500, 68, "Mean vs. peak · 180-second client load · 1 Hz server-side samples", size=14, color="#536273")

    for tick in range(6):
        val = axis_top * tick / 5
        y = bottom - (bottom - top) * tick / 5
        line(x0, y, x1, y)
        label = f"{val:.1f}" if axis_top < 10 else f"{val:.0f}"
        text(x0-15, y+5, label, size=13, color="#536273", anchor="end")
    line(x0, top, x0, bottom, "#475569", 1.5)
    line(x0, bottom, x1, bottom, "#475569", 1.5)
    text(30, 300, units, size=15, color="#536273")

    count = len(rows)
    spacing = available / max(count, 1)
    for j, r in enumerate(rows):
        xcenter = x0 + (j+0.5)*spacing
        bw = min(58, spacing * 0.25)
        for delta, key, color in [(-bw-4, mean_key, "#155e95"), (4, peak_key, "#10a4a3")]:
            value = float(r[key])
            bar_h = (bottom - top) * (value / axis_top)
            xp = xcenter + delta
            pieces.append(f'<rect x="{xp:.1f}" y="{bottom - bar_h:.1f}" width="{bw:.1f}" height="{bar_h:.1f}" rx="3" fill="{color}"/>')
            text(xp+bw/2, bottom - bar_h - 8, f"{value:.2f}" if value < 10 else f"{value:.1f}", size=13, color="#253140")
        text(xcenter, bottom+29, f"{r['clients']} client{'s' if r['clients'] != 1 else ''}", size=17, weight="bold")

    pieces.append('<rect x="310" y="560" width="16" height="16" fill="#155e95"/>')
    text(340, 573, "Average", size=15, anchor="start")
    pieces.append('<rect x="530" y="560" width="16" height="16" fill="#10a4a3"/>')
    text(560, 573, "Peak", size=15, anchor="start")
    pieces.append(f'<text x="500" y="604" font-size="11" fill="#64748b" text-anchor="middle" font-family="Arial, sans-serif">Source: psutil on hw4-server (e2-micro). Values cover client run timestamps.</text>')

    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n' + "\n".join(pieces) + "\n</svg>\n"
    output.write_text(svg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="hw4_stress_results")
    args = parser.parse_args()
    directory = Path(args.results).resolve()
    rows = [r for n in range(1, 5) if (r := summarize_one(n, directory))]
    if not rows:
        raise SystemExit("No complete metrics_{n}.csv / summary_{n}.json pairs found")
    with (directory / "stress_summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    graphs = [
        ("avg_cpu_pct", "peak_cpu_pct", "Server VM CPU usage", "CPU (%)", "cpu_usage.svg"),
        ("avg_memory_used_mb", "peak_memory_used_mb", "Server VM memory usage", "Memory (MB)", "memory_usage.svg"),
        ("avg_bandwidth_mbps", "peak_bandwidth_mbps", "Server VM bandwidth (ingress + egress)", "Network (Mbit/s)", "bandwidth_usage.svg"),
    ]
    for avg, peak, title, units, name in graphs:
        bar_chart(rows, avg, peak, title, units, directory / name)

    print("\nSummary (observed, not estimated):")
    print("Clients | CPU avg/max % | Memory avg/max MB | Network avg/max Mbps | HTTP 200 / 400 / other | process failures")
    for r in rows:
        print(f"{r['clients']:7} | {r['avg_cpu_pct']:5.1f}/{r['peak_cpu_pct']:5.1f} "
              f"| {r['avg_memory_used_mb']:7.1f}/{r['peak_memory_used_mb']:7.1f} "
              f"| {r['avg_bandwidth_mbps']:7.3f}/{r['peak_bandwidth_mbps']:7.3f} "
              f"| {r['http_200']} / {r['expected_http_400']} / {r['other_http_statuses']} "
              f"| {r['client_process_failures']}")
    print("\nGenerated files:")
    for name in ["stress_summary.csv", "cpu_usage.svg", "memory_usage.svg", "bandwidth_usage.svg"]:
        print("-", directory / name)


if __name__ == "__main__":
    main()

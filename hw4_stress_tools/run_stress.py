#!/usr/bin/env python3
"""Run CS528 HW4's 3-minute, 1-4-client VM stress experiment from your Mac.

Requirements: gcloud already authenticated; hw4-server and hw4-client running;
professor's executable at ~/http-client on hw4-client; metrics collector
at /opt/hw4/server/collect_metrics.py on hw4-server (from HW4 deployment).

This script DOES NOT create VMs, change IAM, or increase instance sizes.
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "quick-formula-457319-q3"
ZONE = "us-central1-a"


def execute(command, *, timeout=120, capture=True, must_succeed=True):
    result = subprocess.run(
        command, text=True, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None, timeout=timeout,
        check=False,
    )
    if must_succeed and result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}): {' '.join(command)}\n"
            f"{result.stdout or ''}"
        )
    return result


def ssh_command(instance, command, args):
    return [
        "gcloud", "compute", "ssh", instance,
        f"--project={args.project}", f"--zone={args.zone}",
        f"--command={command}", "--quiet",
    ]


def scp_command(source, target, args):
    return [
        "gcloud", "compute", "scp", source, target,
        f"--project={args.project}", f"--zone={args.zone}", "--quiet",
    ]


def preflight(args):
    if not shutil.which("gcloud"):
        raise RuntimeError("Google Cloud CLI (gcloud) is not in PATH")
    script = Path(__file__).with_name("client_load.sh")
    if not script.is_file():
        raise RuntimeError(f"Missing helper script next to run_stress.py: {script}")

    ip = execute([
        "gcloud", "compute", "instances", "describe", "hw4-server",
        f"--project={args.project}", f"--zone={args.zone}",
        "--format=value(networkInterfaces[0].networkIP)",
    ]).stdout.strip()
    if not ip or not all(s.isdigit() for s in ip.split(".")) or len(ip.split(".")) != 4:
        raise RuntimeError(f"Could not discover the internal server IP: {ip!r}")

    check_client = "test -x ~/http-client && ~/http-client --help >/dev/null && echo client_ready"
    print("Checking professor's client on the client VM...", flush=True)
    print(execute(ssh_command("hw4-client", check_client, args)).stdout.strip())

    check_server = (
        "sudo /opt/hw4/server/.venv/bin/python -c 'import psutil' "
        "&& test -f /opt/hw4/server/collect_metrics.py && echo collector_ready"
    )
    print("Checking server metrics collector...", flush=True)
    print(execute(ssh_command("hw4-server", check_server, args)).stdout.strip())

    check_http = f"curl -fsS -o /dev/null -w '%{{http_code}}' http://{ip}:8080/0.html"
    print("Checking client -> server internal HTTP connectivity...", flush=True)
    status = execute(ssh_command("hw4-client", check_http, args)).stdout.strip()
    if not status.endswith("200"):
        raise RuntimeError(f"GET /0.html expected 200 from {ip}, got: {status!r}")
    print(f"Verified GET 200 from server private IP {ip}")

    print("Uploading small load-test helper to client VM...", flush=True)
    execute(scp_command(str(script), "hw4-client:~/hw4_client_load.sh", args), capture=False)
    return ip


def one_level(args, n, ip, output_dir):
    csv_remote = f"/tmp/hw4_metrics_{n}.csv"
    sample_duration = args.duration + 14
    sample_cmd = (
        "sudo /opt/hw4/server/.venv/bin/python "
        "/opt/hw4/server/collect_metrics.py "
        f"--duration {sample_duration} --output {csv_remote}"
    )
    print(f"\n======== {n} concurrent client(s): {args.duration}s ========", flush=True)
    collector_log = output_dir / f"collector_{n}.log"
    with collector_log.open("w") as fp:
        collector = subprocess.Popen(
            ssh_command("hw4-server", sample_cmd, args),
            stdout=fp, stderr=subprocess.STDOUT, text=True,
        )
        try:
            # Allow the SSH session and psutil collector to begin before the clients.
            time.sleep(5)
            if collector.poll() is not None:
                raise RuntimeError(
                    f"Metrics collector exited early; inspect {collector_log}:\n"
                    f"{collector_log.read_text(errors='replace')}"
                )
            cmd = f"bash ~/hw4_client_load.sh {n} {args.duration} {ip}"
            started = time.monotonic()
            result = execute(
                ssh_command("hw4-client", cmd, args),
                timeout=args.duration + 80, must_succeed=False,
            )
            print(result.stdout or "[no stdout]", flush=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Client VM run failed (exit {result.returncode}); "
                    f"review output above. The client process timeouts prevent "
                    f"unlimited background load."
                )
            print(f"Client VM test returned after {time.monotonic() - started:.1f}s", flush=True)
            collector.wait(timeout=sample_duration + 30)
            if collector.returncode != 0:
                raise RuntimeError(
                    f"Metrics collector failed; inspect {collector_log}:\n"
                    f"{collector_log.read_text(errors='replace')}"
                )
        except Exception:
            # Don't start any additional clients after an orchestration failure.
            if collector.poll() is None:
                collector.terminate()
                try:
                    collector.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    collector.kill()
            raise

    metrics_local = output_dir / f"metrics_{n}.csv"
    summary_local = output_dir / f"summary_{n}.json"
    execute(scp_command(f"hw4-server:{csv_remote}", str(metrics_local), args), capture=False)
    execute(scp_command(
        f"hw4-client:~/hw4_stress/stress_{n}_summary.json", str(summary_local), args
    ), capture=False)
    summary = json.loads(summary_local.read_text())
    print(
        f"Saved: {metrics_local.name} ({sum(1 for _ in metrics_local.open())-1} samples), "
        f"{summary_local.name}\n"
        f"HTTP 200={summary['successful_200']}, "
        f"expected HTTP 400={summary['expected_denied_400']}, "
        f"other HTTP responses={summary['other_http_responses']}, "
        f"client process failures={summary['client_process_failures']}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--zone", default=ZONE)
    parser.add_argument("--duration", type=int, default=180)
    parser.add_argument("--levels", nargs="+", type=int, default=[1, 2, 3, 4])
    parser.add_argument("--output", default="hw4_stress_results")
    args = parser.parse_args()
    if args.duration < 1 or args.duration > 300:
        parser.error("--duration must be between 1 and 300")
    if any(n not in (1, 2, 3, 4) for n in args.levels):
        parser.error("--levels must contain only 1, 2, 3 or 4")
    if sorted(set(args.levels)) != sorted(args.levels):
        parser.error("--levels must be unique and ascending")
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print("CS528 HW4: sequential stress tests using the professor's executable")
    print(f"Project: {args.project} | Zone: {args.zone} | Server: e2-micro")
    print(f"Levels: {args.levels} | {args.duration} seconds each")
    print("Maximum concurrent HTTP client processes: 4 (no VMs created)", flush=True)
    try:
        ip = preflight(args)
        for n in args.levels:
            one_level(args, n, ip, output_dir)
        print("\nGenerating summary CSV and SVG performance charts...", flush=True)
        analysis_cmd = [sys.executable, str(Path(__file__).with_name("analyze_stress.py")),
                        "--results", str(output_dir)]
        execute(analysis_cmd, capture=False, timeout=60)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"\nSTOPPED: {exc}", file=sys.stderr)
        print("No further concurrency levels were started.", file=sys.stderr)
        print(f"Partial results (if any): {output_dir}", file=sys.stderr)
        return 1
    print(f"\nDONE. Results saved in: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

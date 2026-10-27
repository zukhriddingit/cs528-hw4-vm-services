#!/usr/bin/env python3
"""Bash-spawned heartbeat for a long two-client test; easy to terminate by PID."""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def is_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('start_id', type=int)
    parser.add_argument('client1_pid', type=int)
    parser.add_argument('client2_pid', type=int)
    parser.add_argument('hw5_dir', type=Path)
    args = parser.parse_args()
    while True:
        time.sleep(120)
        if not (is_running(args.client1_pid) or is_running(args.client2_pid)):
            return
        stamp = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
        try:
            result = subprocess.run(
                [sys.executable, str(args.hw5_dir / 'request_statistics.py'), '--max-id'],
                text=True, capture_output=True, timeout=45, check=True,
            )
            current_id = int(result.stdout.strip())
            print(f'{stamp}: at least {current_id - args.start_id:,} rows since start (including other traffic).', flush=True)
        except (subprocess.SubprocessError, ValueError):
            print(f'{stamp}: load still running; database progress temporarily unavailable.', flush=True)


if __name__ == '__main__':
    main()

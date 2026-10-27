#!/usr/bin/env python3
"""Run two *original course-client* Python bytecode instances on a Linux GCE VM.

Both processes run as Docker Python3.12 jobs. Start/stop SQL and pause/resume
hourly safety scheduler from within GCP; copy evidence to GCS, then stop this VM.
"""
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

import cloud_vm_ops as cloud

BASE = Path('/opt/hw5/client')
CONFIG = json.loads((BASE / 'config.json').read_text())
COUNT = int(CONFIG['requests_per_client'])
SEED = int(CONFIG.get('seed', 5285))
SERVER_IP = CONFIG['server_internal_ip']
RUN = BASE / 'evidence' / f'vm_load_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}_{COUNT}x2'
RUN.mkdir(parents=True, exist_ok=True)
PYTHON = str(BASE / '.venv' / 'bin' / 'python')
CHILDREN = []
PAUSE_ATTEMPTED = False
SQL_ATTEMPTED = False
CLIENTS_STARTED = False
SHUTDOWN_SCHEDULED = False


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write(name, value):
    (RUN / name).write_text(str(value) + '\n')


def do(args, out=None, *, check=True):
    print(f'RUN: {args[0]} {args[1:4]} ...', flush=True)
    if out:
        with (RUN / out).open('w') as handle:
            cp = subprocess.run(args, stdout=handle, stderr=subprocess.STDOUT, check=check)
    else:
        cp = subprocess.run(args, check=check)
    return cp


def max_id():
    return int(subprocess.check_output([PYTHON, str(BASE / 'request_statistics.py'), '--max-id'],
                                      text=True, timeout=90).strip())


def spawn_course_client(n):
    argv = [
        'docker', 'run', '--rm', '--network=host',
        '--mount', f'type=bind,src={BASE / "course-client.pyc"},dst=/work/http-client.pyc,readonly',
        'python:3.12-slim-bookworm', 'python', '-u', '/work/http-client.pyc',
        '--domain', SERVER_IP, '--bucket', 'none', '--webdir', 'none',
        '--num_requests', str(COUNT), '--index', '11999', '--port', '8080',
        '--random', str(SEED), '--timeout', '30',
    ]
    logfile = (RUN / f'client_{n}.log').open('w')
    proc = subprocess.Popen(argv, stdout=logfile, stderr=subprocess.STDOUT)
    logfile.close()
    CHILDREN.append(proc)
    return proc


def log_status(payload):
    p = RUN / 'run_status.json'
    p.write_text(json.dumps(payload, indent=2) + '\n')


def main():
    global PAUSE_ATTEMPTED, SQL_ATTEMPTED, CLIENTS_STARTED
    print(f'VM load {COUNT} x 2; same course-client bytecode; seed={SEED}; server={SERVER_IP}', flush=True)
    print(f'UTC start: {now()}', flush=True)
    # Confirm that the original professor's Python 3.12 code is runnable on Linux.
    do(['docker', 'run', '--rm',
        '--mount', f'type=bind,src={BASE / "course-client.pyc"},dst=/work/http-client.pyc,readonly',
        'python:3.12-slim-bookworm', 'python', '/work/http-client.pyc', '--help'],
        out='course_client_help.txt')
    log_status({'phase': 'preflight_passed', 'requested_per_client': COUNT, 'seed': SEED})
    # Mark the scheduler for restoration before attempting a pause (reply can be lost).
    PAUSE_ATTEMPTED = True
    cloud.scheduler('pause')
    SQL_ATTEMPTED = True
    cloud.set_activation('ALWAYS')
    password = BASE / '.db_password'
    password.write_bytes(cloud.password())
    os.chmod(password, 0o600)
    # Verify that GCS+server+PostgreSQL are ready before starting the real run.
    from urllib.request import Request, urlopen
    with urlopen(Request(f'http://{SERVER_IP}:8080/0.html', headers={'X-country': 'USA'}), timeout=45) as resp:
        if resp.status != 200:
            raise RuntimeError(f'Bad server response: {resp.status}')
        resp.read()
    initial_id = max_id()
    initial_time = now()
    write('after_request_id.txt', initial_id)
    write('start_utc.txt', initial_time)
    t0 = time.monotonic()
    print(f'Launching concurrent clients: {COUNT} + {COUNT}; start request_id={initial_id}', flush=True)
    CLIENTS_STARTED = True
    p1 = spawn_course_client(1)
    p2 = spawn_course_client(2)
    while p1.poll() is None or p2.poll() is None:
        time.sleep(120)
        try:
            latest = max_id()
            print(f'{now()}: {latest - initial_id:,} new database rows (including incidental traffic)', flush=True)
        except Exception as exc:
            print(f'Progress temporarily unavailable: {type(exc).__name__}: {exc}', flush=True)
    codes = [p1.wait(), p2.wait()]
    elapsed = time.monotonic() - t0
    end_time = now()
    write('client_1.exit_code', codes[0])
    write('client_2.exit_code', codes[1])
    write('end_utc.txt', end_time)
    finish_id = max_id()
    write('through_request_id.txt', finish_id)
    print(f'Clients completed: exit codes {codes}, {elapsed:.0f} seconds', flush=True)
    stat_args = [PYTHON, str(BASE / 'request_statistics.py'), '--after-id', str(initial_id),
                 '--through-id', str(finish_id), '--expected', str(COUNT * 2),
                 '--json-out', str(RUN / 'request_statistics.json')]
    with (RUN / 'request_statistics.txt').open('w') as handle:
        report = subprocess.run(stat_args, stdout=handle, stderr=subprocess.STDOUT, timeout=300)
    if report.returncode:
        raise RuntimeError('Request statistics could not be collected')
    stats = json.loads((RUN / 'request_statistics.json').read_text())
    measured = stats['rows_matching_course_client_headers']
    print(f'Client rows recorded: {measured:,}/{COUNT*2:,}', flush=True)
    print(f'Successful 200: {stats["success_200"]:,}; failed: {stats["unsuccessful_non_200"]:,}', flush=True)
    print(f'Start UTC: {initial_time}; end UTC: {end_time}', flush=True)
    passed = measured == COUNT * 2 and codes == [0, 0]
    log_status({'phase': 'completed', 'passed': passed, 'client_exit_codes': codes,
                'recorded_client_rows': measured, 'expected_rows': COUNT * 2,
                'elapsed_seconds': round(elapsed, 3), 'run_folder': RUN.name,
                'start_utc': initial_time, 'end_utc': end_time,
                'after_request_id': initial_id, 'through_request_id': finish_id})
    if not passed:
        raise RuntimeError(f'Run incomplete: {measured:,}/{COUNT*2:,} rows; exits {codes}')
    print('LOAD TEST PASSED. Preserving evidence and restoring SQL guard...', flush=True)


def cleanup():
    for proc in CHILDREN:
        if proc.poll() is None:
            proc.terminate()
    for proc in CHILDREN:
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
    if SQL_ATTEMPTED:
        try:
            cloud.set_activation('NEVER')
        except Exception as exc:
            print(f'CRITICAL: Could not confirm Cloud SQL shutdown: {exc}', flush=True)
            write('SQL_STOP_UNCONFIRMED.txt', str(exc))
    if PAUSE_ATTEMPTED:
        try:
            cloud.scheduler('resume')
        except Exception as exc:
            print(f'CRITICAL: Could not confirm Scheduler re-enable: {exc}', flush=True)
            write('SCHEDULER_RESUME_UNCONFIRMED.txt', str(exc))
    (BASE / '.db_password').unlink(missing_ok=True)
    # Upload all existing artifacts; each filename includes a unique run directory.
    remote = f'hw5_evidence/{RUN.name}'
    for path in sorted(RUN.iterdir()):
        if not path.is_file():
            continue
        try:
            cloud.upload_file(path, f'{remote}/{path.name}')
        except Exception as exc:
            print(f'WARNING: Evidence upload failed for {path.name}: {exc}', flush=True)
    print(f'Evidence location: gs://{cloud.BUCKET}/{remote}/', flush=True)
    # Automatically stop client VM to prevent compute billing. Disk is preserved.
    print('Shutting down client VM to prevent further compute charges.', flush=True)
    subprocess.run(['shutdown', '-h', '+1'], check=False)


def interrupt(signum, frame):
    print(f'Signal {signum} received; cleanly stopping clients and database.', flush=True)
    raise KeyboardInterrupt

if __name__ == '__main__':
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, interrupt)
    status = 1
    try:
        main()
        status = 0
    except BaseException as exc:
        print(f'VM load error: {exc}', flush=True)
        traceback.print_exc()
        try:
            log_status({'phase': 'failed', 'error': str(exc), 'timestamp_utc': now()})
        except Exception:
            pass
    finally:
        cleanup()
    sys.exit(status)

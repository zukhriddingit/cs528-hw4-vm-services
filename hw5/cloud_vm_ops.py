#!/usr/bin/env python3
"""Cloud SQL, Scheduler, Secret Manager and GCS actions using the VM service account.

Uses metadata-server credentials, not service-account keys or interactive login.
"""
import base64
import json
import time
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT = 'quick-formula-457319-q3'
REGION = 'us-central1'
SQL_INSTANCE = 'hw5-sql'
JOB = 'hw5-hourly-sql-stop'
SECRET = 'hw5-db-password'
BUCKET = 'cloud_hw2_bucket'
SQL_BASE = f'https://sqladmin.googleapis.com/sql/v1beta4/projects/{PROJECT}'
SCHED_BASE = f'https://cloudscheduler.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/jobs/{JOB}'


def token():
    req = Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
                  headers={'Metadata-Flavor': 'Google'})
    with urlopen(req, timeout=15) as resp:
        return json.load(resp)['access_token']


def fetch(method, url, body=None, *, retry_get=True, timeout=50):
    data = None if body is None else json.dumps(body).encode('utf-8')
    tries = 4 if method == 'GET' and retry_get else 1
    for attempt in range(tries):
        req = Request(url, method=method, data=data,
                      headers={'Authorization': f'Bearer {token()}',
                               'Content-Type': 'application/json; charset=utf-8'})
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt + 1 == tries:
                raise RuntimeError(f'{method} {url} failed: {exc}') from exc
            time.sleep(5 * (attempt + 1))


def instance():
    return fetch('GET', f'{SQL_BASE}/instances/{SQL_INSTANCE}')


def wait_operation(name, timeout=2100):
    operation = name.rsplit('/', 1)[-1]
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        result = fetch('GET', f'{SQL_BASE}/operations/{operation}')
        if result.get('status') == 'DONE':
            if result.get('error'):
                raise RuntimeError(f'Cloud SQL operation failed: {result["error"]}')
            return
        time.sleep(10)
    raise TimeoutError(f'Cloud SQL operation {operation} did not finish in {timeout}s')


def wait_pending(timeout=2100):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        ops = fetch('GET', f'{SQL_BASE}/operations?instance={SQL_INSTANCE}&maxResults=30').get('items', [])
        pending = [op['name'] for op in ops if op.get('status') in ('PENDING', 'RUNNING')]
        if not pending:
            return
        for name in pending:
            print(f'Waiting for previous SQL operation {name}', flush=True)
            wait_operation(name, timeout=max(30, int(timeout - (time.monotonic() - start))))
    raise TimeoutError('Prior SQL operations did not finish')


def set_activation(policy):
    wait_pending()
    current = instance()
    state = current.get('state')
    cur_policy = current.get('settings', {}).get('activationPolicy')
    if policy == cur_policy and ((policy == 'NEVER' and state == 'STOPPED') or
                                (policy == 'ALWAYS' and state == 'RUNNABLE')):
        print(f'Cloud SQL already {state} / {cur_policy}', flush=True)
        return
    print(f'Changing Cloud SQL activation to {policy}', flush=True)
    op = fetch('PATCH', f'{SQL_BASE}/instances/{SQL_INSTANCE}', {'settings': {'activationPolicy': policy}})
    if not op.get('name'):
        raise RuntimeError('SQL Admin patch did not return an operation name')
    wait_operation(op['name'])
    target = 'STOPPED' if policy == 'NEVER' else 'RUNNABLE'
    state = instance().get('state')
    if state != target:
        raise RuntimeError(f'Expected Cloud SQL {target}, got {state}')
    print(f'Cloud SQL now {state} / {policy}', flush=True)


def scheduler(action):
    if action not in ('pause', 'resume'):
        raise ValueError(action)
    print(f'Cloud Scheduler {action}', flush=True)
    current = fetch('GET', SCHED_BASE).get('state')
    target = 'PAUSED' if action == 'pause' else 'ENABLED'
    if current == target:
        print(f'Cloud Scheduler already {target}', flush=True)
        return
    result = fetch('POST', f'{SCHED_BASE}:{action}', {})
    print(f'Cloud Scheduler state: {result.get("state", "unknown")}', flush=True)


def password():
    url = f'https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/{SECRET}/versions/latest:access'
    data = fetch('GET', url)
    return base64.b64decode(data['payload']['data'])


def upload_file(local_path: Path, name: str):
    data = local_path.read_bytes()
    url = f'https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?uploadType=media&name={quote(name, safe="")}'
    # Upload one file; retries only possible safely using distinct object names.
    req = Request(url, method='POST', data=data, headers={
        'Authorization': f'Bearer {token()}',
        'Content-Type': 'application/octet-stream',
    })
    with urlopen(req, timeout=180) as resp:
        result = json.load(resp)
    print(f'Uploaded gs://{BUCKET}/{result["name"]}', flush=True)

#!/usr/bin/env python3
"""CS528 HW5: request-ID bounded Cloud SQL statistics for one load test.

This is read-only. The optional client-header filter excludes unrelated public
internet traffic that may reach the server while a run is in progress.
"""

import argparse
import json
from pathlib import Path

from google.cloud.sql.connector import Connector, IPTypes
from setup_schema import DB_NAME, DB_USER, INSTANCE_CONNECTION_NAME, get_credentials


def connect_database():
    password_path = Path(__file__).with_name('.db_password')
    if not password_path.is_file():
        raise SystemExit(f'Missing DB password file: {password_path}')
    password = password_path.read_text(encoding='utf-8').strip()
    if not password:
        raise SystemExit('DB password file is empty')
    connector = Connector(credentials=get_credentials(), refresh_strategy='LAZY')
    try:
        connection = connector.connect(
            INSTANCE_CONNECTION_NAME, 'pg8000', db=DB_NAME,
            user=DB_USER, password=password, ip_type=IPTypes.PUBLIC,
        )
    except Exception:
        connector.close()
        raise
    return connector, connection


def one(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def group_counts(cur, column_sql, where_sql, params, *, limit=None):
    sql = (f'SELECT {column_sql} AS group_name, COUNT(*) AS n '
           f'FROM requests WHERE {where_sql} '
           f'GROUP BY group_name ORDER BY n DESC, group_name ASC')
    if limit:
        sql += f' LIMIT {int(limit)}'
    cur.execute(sql, params)
    return [{'name': str(name) if name is not None else 'Unknown',
             'count': int(count)} for name, count in cur.fetchall()]


def summarize(cur, start_id, end_id, expected):
    bounds = (start_id, end_id)
    in_range = 'request_id > %s AND request_id <= %s'
    client_header_predicate = (
        "country IS NOT NULL AND gender IN ('Male', 'Female') "
        'AND age IS NOT NULL AND income IS NOT NULL AND time_of_day IS NOT NULL'
    )
    included = f'{in_range} AND {client_header_predicate}'
    raw = one(cur, f'SELECT COUNT(*) FROM requests WHERE {in_range}', bounds)
    total = one(cur, f'SELECT COUNT(*) FROM requests WHERE {included}', bounds)

    # Status is recorded on all requests, not just the error table.
    cur.execute(f'''
        SELECT COUNT(*) FILTER (WHERE status_code = 200),
               COUNT(*) FILTER (WHERE status_code <> 200),
               COUNT(*) FILTER (WHERE is_banned)
        FROM requests WHERE {included}
    ''', bounds)
    success, failure, banned = (int(value) for value in cur.fetchone())
    status_counts = group_counts(cur, 'status_code', included, bounds)
    gender_counts = group_counts(cur, "COALESCE(gender, 'Unknown')", included, bounds)
    top_countries = group_counts(cur, "COALESCE(country, 'Unknown')", included, bounds, limit=5)
    age_counts = group_counts(cur, "COALESCE(age, 'Unknown')", included, bounds)
    income_counts = group_counts(cur, "COALESCE(income, 'Unknown')", included, bounds)

    def winners(groups):
        if not groups:
            return []
        top = groups[0]['count']
        return [group for group in groups if group['count'] == top]

    return {
        'run_bounds': {'after_request_id': start_id, 'through_request_id': end_id},
        'expected_client_requests': expected,
        'rows_in_id_range': int(raw),
        'rows_matching_course_client_headers': int(total),
        'other_rows_in_id_range': int(raw - total),
        'possible_missing_client_requests': max(0, expected - total) if expected is not None else None,
        'success_200': success,
        'unsuccessful_non_200': failure,
        'banned_country_requests': banned,
        'http_statuses': status_counts,
        'gender_counts': gender_counts,
        'top_5_countries': top_countries,
        'age_group_counts': age_counts,
        'most_active_age_group': winners(age_counts),
        'income_group_counts': income_counts,
        'most_active_income_group': winners(income_counts),
        'filter_definition': client_header_predicate,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-id', action='store_true', help='Print current largest request_id only')
    parser.add_argument('--after-id', type=int, help='Exclusive lower request ID bound')
    parser.add_argument('--through-id', type=int, help='Inclusive upper request ID bound')
    parser.add_argument('--expected', type=int, default=None, help='Expected requests from both clients')
    parser.add_argument('--json-out', type=Path, help='Write machine-readable summary')
    args = parser.parse_args()
    if not args.max_id and (args.after_id is None or args.through_id is None):
        parser.error('Provide --max-id, or both --after-id and --through-id')
    if args.after_id is not None and args.after_id < 0:
        parser.error('--after-id must not be negative')
    if args.through_id is not None and args.after_id is not None and args.through_id < args.after_id:
        parser.error('--through-id must be >= --after-id')

    connector, connection = connect_database()
    try:
        cur = connection.cursor()
        try:
            if args.max_id:
                print(one(cur, 'SELECT COALESCE(MAX(request_id), 0) FROM requests'))
                return
            summary = summarize(cur, args.after_id, args.through_id, args.expected)
        finally:
            cur.close()
    finally:
        connection.close()
        connector.close()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    print(f"Request IDs: ({args.after_id}, {args.through_id}]")
    print(f"All rows in ID range: {summary['rows_in_id_range']:,}")
    print(f"Rows matching course-client headers: {summary['rows_matching_course_client_headers']:,}")
    print(f"Other (e.g. internet scanning) rows: {summary['other_rows_in_id_range']:,}")
    if args.expected is not None:
        print(f'Expected client requests: {args.expected:,}')
        difference = args.expected - summary['rows_matching_course_client_headers']
        if difference:
            print(f'CHECK: expected minus recorded client-like rows = {difference:+,}')
    print(f"HTTP 200 successful: {summary['success_200']:,}")
    print(f"HTTP !=200 unsuccessful: {summary['unsuccessful_non_200']:,}")
    print(f"Banned-country requests: {summary['banned_country_requests']:,}")
    for heading, key in [
        ('HTTP status counts', 'http_statuses'),
        ('Gender', 'gender_counts'),
        ('Top 5 countries', 'top_5_countries'),
        ('Age groups', 'age_group_counts'),
        ('Income groups', 'income_group_counts'),
    ]:
        print('\n' + heading + ':')
        for item in summary[key]:
            print(f"  {item['name']}: {item['count']:,}")
    for heading, key in [
        ('Most active age group', 'most_active_age_group'),
        ('Most active income group', 'most_active_income_group'),
    ]:
        names = ', '.join(f"{item['name']} ({item['count']:,})" for item in summary[key])
        print(f'{heading}: {names or "n/a"}')
    print('\nNotes: Uses strictly recorded HTTP status codes. Rows without the course client’s')
    print('expected demographic headers are excluded but separately counted above.')
    print('Any client/network failure that never reached PostgreSQL cannot appear in these counts.')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Generate a CS528 HW5 PDF report from the saved evidence files.

Usage (from the repository root):
    python3 -m pip install reportlab
    python3 hw5/generate_report.py

Prefers the newest COMPLETED load_*_50000x2 directory. If unavailable,
uses the newest completed pilot and clearly labels the report a DRAFT.
It never contacts GCP or changes infrastructure.

Optional:
    python3 hw5/generate_report.py --run-dir hw5/evidence/load_..._50000x2 \
        --cost-usd 12.34 --output HW5_report.pdf

Actual screenshots can be placed in hw5/evidence/screenshots/ as PNG or JPG;
they will be appended to the report automatically. The supplied curl transcript
is also summarized regardless of screenshots.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable, Image, Preformatted
)

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'evidence'
NAVY = colors.HexColor('#182A45')
BLUE = colors.HexColor('#195EA8')
BLUE_LIGHT = colors.HexColor('#EAF2FA')
SLATE = colors.HexColor('#536174')
BORDER = colors.HexColor('#DFE6EF')
VERY_LIGHT = colors.HexColor('#F6F8FB')
GREEN = colors.HexColor('#176C55')
MARGIN = 47
PAGE_W, PAGE_H = letter
WIDTH = PAGE_W - 2*MARGIN

PILOT = {
  'expected_client_requests': 200,
  'rows_in_id_range':200,
  'rows_matching_course_client_headers':200,
  'other_rows_in_id_range':0,
  'success_200':190,
  'unsuccessful_non_200':10,
  'banned_country_requests':10,
  'http_statuses':[{'name':'200','count':190},{'name':'400','count':10}],
  'gender_counts':[{'name':'Female','count':103},{'name':'Male','count':97}],
  'top_5_countries':[{'name':'Antigua and Barbuda','count':4},{'name':'Bosnia and Herzegovina','count':4},{'name':'Algeria','count':3},{'name':'Angola','count':3},{'name':'Barbados','count':3}],
  'age_group_counts':[{'name':'66-75','count':31},{'name':'36-45','count':27},{'name':'46-55','count':27},{'name':'76+','count':27},{'name':'26-35','count':26},{'name':'17-25','count':25},{'name':'0-16','count':19},{'name':'56-65','count':18}],
  'income_group_counts':[{'name':'250k+','count':33},{'name':'10k-20k','count':29},{'name':'40k-60k','count':29},{'name':'60k-100k','count':27},{'name':'150k-250k','count':23},{'name':'100k-150k','count':22},{'name':'0-10k','count':19},{'name':'20k-40k','count':18}],
  'most_active_age_group':[{'name':'66-75','count':31}],
  'most_active_income_group':[{'name':'250k+','count':33}],
}
PILOT_TIMINGS = [
    ('Header extraction', 0.0489, 0.0432, 0.0691),
    ('Cloud Storage read', 101.8928, 92.2788, 189.3836),
    ('Cloud SQL insert/transaction', 14.4234, 11.5366, 15.9542),
    ('Response object preparation', 0.0309, 0.0284, 0.0447),
    ('WSGI response-send interval', 53.5929, 80.5870, 126.9004),
    ('Total server processing', 178.7628, 188.9821, 312.0694),
]
TIMING_COLS = [
    ('header_ms','Header extraction'),
    ('storage_ms','Cloud Storage read'),
    ('database_ms','Cloud SQL insert/transaction'),
    ('response_prepare_ms','Response preparation'),
    ('response_send_ms','WSGI response-send interval'),
    ('total_ms','Total server processing'),
]

S = getSampleStyleSheet()
S.add(ParagraphStyle(name='CoverKicker',fontName='Helvetica-Bold',fontSize=10,leading=14,textColor=BLUE,spaceAfter=12))
S.add(ParagraphStyle(name='CoverTitle',fontName='Helvetica-Bold',fontSize=25,leading=32,textColor=NAVY,spaceAfter=15))
S.add(ParagraphStyle(name='CoverSub',fontName='Helvetica',fontSize=12,leading=18,textColor=SLATE,spaceAfter=18))
S.add(ParagraphStyle(name='H1x',fontName='Helvetica-Bold',fontSize=15,leading=19,textColor=NAVY,spaceBefore=19,spaceAfter=9,keepWithNext=True))
S.add(ParagraphStyle(name='H2x',fontName='Helvetica-Bold',fontSize=11,leading=15,textColor=BLUE,spaceBefore=12,spaceAfter=6,keepWithNext=True))
S.add(ParagraphStyle(name='Bodyx',fontName='Helvetica',fontSize=9.1,leading=13.7,textColor=NAVY,spaceAfter=8))
S.add(ParagraphStyle(name='Smallx',fontName='Helvetica',fontSize=8.0,leading=11.6,textColor=SLATE,spaceAfter=6))
S.add(ParagraphStyle(name='Fine',fontName='Helvetica',fontSize=7,leading=10.5,textColor=SLATE,spaceAfter=5))
S.add(ParagraphStyle(name='Cellx',fontName='Helvetica',fontSize=8,leading=10.5,textColor=NAVY))
S.add(ParagraphStyle(name='HeadCell',fontName='Helvetica-Bold',fontSize=8,leading=11,textColor=colors.white))
S.add(ParagraphStyle(name='MetricNum',fontName='Helvetica-Bold',fontSize=15,leading=20,textColor=NAVY,alignment=TA_CENTER))
S.add(ParagraphStyle(name='MetricLabel',fontName='Helvetica',fontSize=8,leading=12,textColor=SLATE,alignment=TA_CENTER))
S.add(ParagraphStyle(name='CodeLine',fontName='Courier',fontSize=7.1,leading=10.3,textColor=NAVY))


def esc(x):
    return html.escape(str(x), quote=False)


def par(txt, style='Bodyx'):
    return Paragraph(txt, S[style])


def heading(txt, level=1):
    return par(esc(txt), 'H1x' if level==1 else 'H2x')


def make_table(rows, widths=None, header=True, col_align=None):
    formatted=[]
    for ri,row in enumerate(rows):
        formatted.append([par(esc(value), 'HeadCell' if ri==0 and header else 'Cellx') for value in row])
    t=Table(formatted, colWidths=widths, hAlign='LEFT', repeatRows=1 if header else 0)
    cmds=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),9),
          ('RIGHTPADDING',(0,0),(-1,-1),9),('TOPPADDING',(0,0),(-1,-1),7),
          ('BOTTOMPADDING',(0,0),(-1,-1),7),('LINEBELOW',(0,-1),(-1,-1),.6,BORDER)]
    if header:
        cmds += [('BACKGROUND',(0,0),(-1,0),NAVY),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,VERY_LIGHT])]
    else:
        cmds += [('ROWBACKGROUNDS',(0,0),(-1,-1),[colors.white,VERY_LIGHT])]
    if col_align:
        for ix in col_align:
            cmds.append(('ALIGN',(ix,1),(ix,-1),'RIGHT'))
    t.setStyle(TableStyle(cmds))
    return t


def metric_cards(items):
    units=[]
    n=len(items)
    for lbl,val in items:
        units.append([par(esc(val),'MetricNum'), par(esc(lbl),'MetricLabel')])
    tb=Table([units],colWidths=[WIDTH/n]*n)
    tb.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),BLUE_LIGHT),('BOX',(0,0),(-1,-1),.6,BORDER),
        ('INNERGRID',(0,0),(-1,-1),.5,colors.white),('TOPPADDING',(0,0),(-1,-1),13),
        ('BOTTOMPADDING',(0,0),(-1,-1),11)]))
    return tb


def footer(canvas,doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.line(MARGIN,37,PAGE_W-MARGIN,37)
    canvas.setFont('Helvetica',7.5)
    canvas.setFillColor(SLATE)
    canvas.drawString(MARGIN,25,'CS 528 Cloud Computing  |  Homework 5  |  Zukhriddin Fakhriddinov')
    canvas.drawRightString(PAGE_W-MARGIN,25,f'Page {doc.page}')
    canvas.restoreState()


def select_run(explicit=None):
    if explicit:
        d=Path(explicit).resolve()
        if not (d/'request_statistics.json').exists():
            raise SystemExit(f'Missing request_statistics.json in {d}')
        return d
    ds=sorted(EVIDENCE.glob('load_*_50000x2'), reverse=True)
    ds += sorted((d for d in EVIDENCE.glob('load_*_100x2') if d not in ds), reverse=True)
    for d in ds:
        if (d/'request_statistics.json').is_file():
            return d
    return None


def get_timing_records(csv_path):
    if not csv_path.is_file():
        return PILOT_TIMINGS, 200, True
    series={k:[] for k,_ in TIMING_COLS}
    n=0
    with csv_path.open(newline='',encoding='utf-8') as f:
        for record in csv.DictReader(f):
            n+=1
            for col in series:
                raw=record.get(col)
                if raw:
                    try:series[col].append(float(raw))
                    except ValueError:pass
    data=[]
    for col,label in TIMING_COLS:
        vals=sorted(series[col]);
        if not vals:continue
        data.append((label,statistics.fmean(vals),statistics.median(vals),vals[max(0,int((len(vals)-1)*.95))]))
    if not data:
        return PILOT_TIMINGS,200,True
    return data,n,False


def pairs(data):
    return [(str(x['name']),int(x['count'])) for x in data]


def report(stats, timings, measured_n, run_dir, draft, output, cost_usd):
    doc=BaseDocTemplate(str(output), pagesize=letter,
        leftMargin=MARGIN,rightMargin=MARGIN,topMargin=50,bottomMargin=53,
        title='CS 528 Homework 5 - Cloud SQL Request Analytics',
        author='Zukhriddin Fakhriddinov')
    frame=Frame(MARGIN,53,WIDTH,PAGE_H-103,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id='Normal',frames=[frame],onPage=footer))
    elems=[]
    add=elems.append
    add(Spacer(1,25))
    add(par('BOSTON UNIVERSITY  /  CS 528 CLOUD COMPUTING','CoverKicker'))
    add(par('Homework 5<br/>Cloud SQL Request Logging &amp; Performance Analysis','CoverTitle'))
    add(par('Implementation, deployment instructions, database schema, functional verification, request analytics, and timing instrumentation.','CoverSub'))
    add(HRFlowable(width='100%',thickness=2,color=BLUE,spaceAfter=18))
    add(make_table([
        ['Submission detail','Value'],
        ['Student','Zukhriddin Fakhriddinov'],
        ['GCP Project','quick-formula-457319-q3'],
        ['Region / zone','us-central1 / us-central1-a'],
        ['Repository','github.com/zukhriddingit/cs528-hw4-vm-services/tree/main/hw5'],
        ['Report prepared',datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')],
        ['Evidence status',('PILOT ONLY - full 100,000-request run not yet included' if draft else 'Full workload statistics - check expected count below')],
    ],[130,WIDTH-130]))
    add(Spacer(1,20))
    add(par('<b>Evidence integrity:</b> All measured figures in this report are from the saved course-client experiment. If the full run has not completed, pilot measurements are explicitly identified and must not be presented as 100,000-request results.','Smallx'))
    add(PageBreak())

    add(heading('1. System architecture and continuity from HW4'))
    add(par('The HW4 Flask/Gunicorn file-serving application was extended to persist HTTP request metadata to PostgreSQL in Cloud SQL. HTML content continues to be served from the existing Google Cloud Storage bucket, and the HW4 Pub/Sub reporter (second application) is kept unchanged. Each incoming request creates a row in <b>requests</b>; requests with non-200 status additionally create a row in <b>failed_requests</b>.'))
    add(make_table([
        ['Component','Implementation'],
        ['HTTP web VM','hw5-server; Compute Engine e2-standard-2, Debian 12, Gunicorn/Flask, TCP 8080'],
        ['Reporter VM','hw5-reporter; e2-micro, same HW4 Pub/Sub subscriber source'],
        ['File storage','gs://cloud_hw2_bucket/pages/[file].html'],
        ['SQL persistence','hw5-sql; PostgreSQL 16 Enterprise, db-f1-micro, 10 GB SSD, ZONAL'],
        ['SQL connection','Cloud SQL Python Connector (pg8000), SQLAlchemy pool; credentials via Secret Manager'],
        ['Forbidden reporting','X-country denylist; Pub/Sub topic hw3-forbidden-requests; reporter subscription'],
        ['Safety mechanism','Authenticated Gen2 Cloud Function + hourly Cloud Scheduler job'],
        ['Clients','Two concurrent instances of the provided macOS ARM64 client, seed 5285'],
    ],[145,WIDTH-145]))
    add(heading('Request processing and authorization',2))
    add(par('The web app extracts X-country, X-client-IP, X-gender, X-age, X-income, and X-time, plus the requested filename. Banned countries are rejected with HTTP 400 and forwarded to the existing Pub/Sub reporting path. Existing pages return 200; missing pages return 404; unsupported methods return 501. An atomic SQL transaction inserts the main request row and, for every non-200 response, an associated failure row.'))
    add(par('The request country/IP/age/income fields are synthetic values provided by the course client, not verified real-world demographic or geolocation data.','Smallx'))

    add(heading('2. Cloud SQL schema'))
    add(par('The database is <b>hw5_requests</b>, owned/initialized by PostgreSQL user <b>hw5_user</b>. Schema creation uses CREATE TABLE IF NOT EXISTS so reruns do not destroy previously logged requests.'))
    add(make_table([
        ['Table','Column','PostgreSQL type / purpose'],
        ['requests','request_id','BIGINT IDENTITY, PRIMARY KEY'],
        ['','country, client_ip','TEXT, INET'],
        ['','gender, age, income','VARCHAR(16) category labels'],
        ['','is_banned','BOOLEAN NOT NULL'],
        ['','time_of_day','TIMESTAMP WITHOUT TIME ZONE'],
        ['','requested_file, status_code','TEXT NOT NULL; SMALLINT NOT NULL'],
        ['','received_at','TIMESTAMPTZ NOT NULL; DEFAULT CURRENT_TIMESTAMP'],
        ['failed_requests','failure_id','BIGINT IDENTITY, PRIMARY KEY'],
        ['','request_time','TIMESTAMPTZ NOT NULL'],
        ['','requested_file, error_code','TEXT NOT NULL; SMALLINT NOT NULL, error_code != 200'],
    ],[98,148,WIDTH-246]))
    add(par('The failure table contains only the three required event attributes plus its surrogate primary key. The application records timestamps in UTC and preserves user-provided age and income bins as text.','Smallx'))

    add(heading('3. Deployment, startup, and teardown instructions'))
    add(par('The grader should use the lowercase <b>hw5/</b> directory. setup.sh and cleanup.sh hardcode the project ID in _common.sh, independent of the grader\'s active gcloud project. A VM startup script is installed using --metadata-from-file and is guarded with a run-once package installation lock. No gcloud compute ssh --command is used for VM provisioning.'))
    add(make_table([
        ['Action','Command from repository root'],
        ['Provision','bash hw5/setup.sh'],
        ['Check VM state','gcloud compute instances list --project=quick-formula-457319-q3'],
        ['Resolve IP','gcloud compute addresses describe hw5-server-ip --region=us-central1 --format="value(address)"'],
        ['Curl verification','bash hw5/verify_curl.sh'],
        ['Run 2 x 50,000 requests','caffeinate -i bash hw5/run_load.sh 50000'],
        ['Analyze saved evidence','python3 hw5/generate_report.py'],
        ['Teardown','bash hw5/cleanup.sh'],
    ],[156,WIDTH-156]))
    add(par('<b>Infrastructure behavior.</b> setup.sh creates or reuses the SQL instance, user, and schema; provisions/reuses service accounts, bucket read permission, Cloud SQL Client access, Pub/Sub publisher/subscriber access, Secret Manager secret access, web VM and reporter VM, allocated static IP, firewall, Gen2 stop function, and Scheduler. It retrieves the allocated IP with gcloud rather than hardcoding an address.'))
    add(par('<b>Startup behavior.</b> startup.sh runs as root on each VM boot. Its lock file prevents repeating apt/pip installation, while source/metadata/service updates still occur on subsequent boots. A Gunicorn web service launches automatically under www-data.'))
    add(par('<b>Cleanup behavior.</b> cleanup.sh stops but does not delete Cloud SQL, removes the HW5 VMs and static IP, and removes the HW5 firewall/subscription/function/scheduler. Legacy HW4 resources and shared HW3 Pub/Sub topic are preserved. No gcloud auth application-default login is used, so ADC revocation is unnecessary.'))
    add(heading('Hourly cloud database safety function',2))
    add(par('An authenticated Cloud Scheduler POST request triggers the Gen2 function <b>hw5-sql-guard</b> at minute 0 of each hour (UTC). The function reads the instance state through the Cloud SQL Admin API and patches activationPolicy to NEVER if the database is RUNNABLE. During long experiments, run_load.sh temporarily pauses this job; its exit trap attempts to stop SQL and resume the job. Network outages can prevent cleanup, so final cloud state must be verified manually if errors occur.'))

    add(heading('4. Curl verification: table contents before and after each request'))
    add(par('On October 27, 2026, the three requested curl demonstrations returned the expected HTTP codes. The table contents were queried immediately before and after each request using inspect_tables.py. Counts below are directly supported by the saved terminal output.'))
    add(make_table([
        ['Case','HTTP','requests before -> after','failed_requests before -> after'],
        ['GET /0.html (USA)','200','0 -> 1','0 -> 0'],
        ['GET /999999.html (USA)','404','1 -> 2','0 -> 1'],
        ['GET /0.html (Iran)','400','2 -> 3','1 -> 2'],
    ],[156,58,150,WIDTH-364]))
    add(par('<b>Recorded rows:</b> request_id 1: file 0.html, USA, status 200, is_banned=false. request_id 2: file 999999.html, USA, status 404, with failed_requests failure_id 1. request_id 3: file 0.html, Iran, status 400, is_banned=true, with failed_requests failure_id 2. The first three request IDs and status codes are verified by the provided terminal transcript.'))
    if (EVIDENCE.exists() and list(EVIDENCE.glob('curl_*.txt'))):
        add(par('The reproducible plain-text transcript is saved under hw5/evidence/curl_*.txt.','Smallx'))
    else:
        add(par('The full terminal transcript was supplied during the interactive test; include the locally saved hw5/evidence/curl_*.txt in the submission evidence.','Smallx'))
    add(par('<b>Screenshot requirement:</b> Attach actual terminal screenshots from the curl run in hw5/evidence/screenshots/. Images found there are appended to this PDF; textual transcript summaries alone are not literal screen captures.','Smallx'))

    add(heading('5. Concurrent-client load test and database statistics'))
    number=int(stats.get('rows_matching_course_client_headers',0))
    expect=stats.get('expected_client_requests') or 200
    label=('Full 100,000-request load experiment' if not draft else 'Validated 200-request PILOT (full load pending)')
    add(par(f'<b>{esc(label)}</b>. Both client processes use the provided course executable with identical random seed 5285. The launch wrapper snapshots the starting request ID and filters all statistics to (start_id, end_id]. This avoids including the earlier curl tests or unrelated traffic.'))
    if run_dir:
        add(par('Evidence directory: '+esc(run_dir.name),'Smallx'))
    add(metric_cards([
        ('Client requests recorded',f'{number:,}'),
        ('HTTP 200',f"{int(stats['success_200']):,}"),
        ('Non-200',f"{int(stats['unsuccessful_non_200']):,}"),
        ('Banned countries',f"{int(stats['banned_country_requests']):,}"),
    ]))
    if number != expect:
        add(par(f'<b>Reconciliation warning:</b> {number:,} course-client-like requests were logged but the expected count was {expect:,}. This experiment must not be reported as complete until reconciled.','Bodyx'))
    else:
        add(par(f'Reconciliation: {number:,} / {expect:,} expected requests appear in the database under the course-client header filter. Other rows in ID range: {stats.get("other_rows_in_id_range",0):,}.','Smallx'))
    add(heading('HTTP status codes and gender split',2))
    status_rows=[['HTTP status','Requests']]+[[n,str(c)] for n,c in pairs(stats.get('http_statuses',[]))]
    gender_rows=[['Gender','Requests']]+[[n,str(c)] for n,c in pairs(stats.get('gender_counts',[]))]
    side=Table([[make_table(status_rows,[110,125]),make_table(gender_rows,[110,125])]], colWidths=[WIDTH/2]*2)
    side.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    add(side)
    add(heading('Five most frequent source countries',2))
    add(make_table([['Country','Requests']]+[[n,str(c)] for n,c in pairs(stats.get('top_5_countries',[]))],[WIDTH-140,140]))
    add(heading('Age and income frequency',2))
    add(make_table([['Age group','Requests']]+[[n,str(c)] for n,c in pairs(stats.get('age_group_counts',[]))],[WIDTH-140,140]))
    add(make_table([['Income group','Requests']]+[[n,str(c)] for n,c in pairs(stats.get('income_group_counts',[]))],[WIDTH-140,140]))
    age=stats.get('most_active_age_group',[]); income=stats.get('most_active_income_group',[])
    age_label = ', '.join(f"{r['name']} ({r['count']:,})" for r in age) or 'unavailable'
    income_label = ', '.join(f"{r['name']} ({r['count']:,})" for r in income) or 'unavailable'
    add(par('<b>Most active age group:</b> '+esc(age_label)+'<br/><b>Most active income group:</b> '+esc(income_label)))
    add(par('A response code of 200 is counted as successful; every other HTTP status is counted as unsuccessful. Network failures that never reached the web server cannot be represented in these tables.','Smallx'))

    add(heading('6. High-resolution web server timing results'))
    add(par('The Flask service records request-stage durations with Python time.perf_counter_ns(). Header extraction, GCS download, SQL transaction, and response construction reside in separate functions. A wrapper around the WSGI iterator records the response-send interval and total server processing duration. Timing records are emitted to a CSV on the web VM and retrieved after the run.'))
    add(par(f'Measured request timing rows: <b>{measured_n:,}</b>. '+ ('Pilot-only timing values are shown.' if draft else 'Values are from the selected full-run timing CSV.')))
    timing_rows=[['Measured stage','Mean (ms)','Median (ms)','P95 (ms)']]
    for label,m,median,p95 in timings:
        timing_rows.append([label,f'{m:.3f}',f'{median:.3f}',f'{p95:.3f}'])
    add(make_table(timing_rows,[WIDTH-258,86,86,86]))
    add(par('P95 is the sample 95th percentile using a nearest-rank-style indexed observation. The WSGI send interval approximates socket write/iterator consumption time inside the server; it is not a measurement of full client-side download completion. Timed stages may exclude ancillary logging and overhead, so stage means need not sum precisely to total processing time.','Smallx'))
    add(heading('7. Resource usage and cloud billing'))
    add(par('The reported infrastructure consists of the web VM (e2-standard-2), reporter VM (e2-micro), PostgreSQL Enterprise (db-f1-micro / 10 GB), Cloud Storage objects/operations, Pub/Sub, Cloud Scheduler/Functions, disk and external IP resources, and outbound data transfer. Database compute is stopped when not in use; storage and some network/IP charges may remain.'))
    if cost_usd is None:
        add(par('<b>Actual billed spend: awaiting finalized Google Cloud Billing data.</b> The assignment explicitly permits submitting <b>billing_repord.pdf</b> to the GitHub repository the following day because cloud billing data can be delayed. No unverified monetary amount is claimed here.','Bodyx'))
    else:
        add(par(f'<b>Actual reported total GCP spend: USD ${cost_usd:.2f}</b>. Verify the corresponding billing-console screenshot/export before submission.','Bodyx'))
    add(par('Billing reproducibility: open Google Cloud Billing, select project quick-formula-457319-q3, set the custom date range to cover HW5 development/execution, and group charges by service. Include any costs related to these resources and note that earlier HW4 resources might also generate charges if left allocated.','Smallx'))
    add(heading('8. Reproducibility and evidence files'))
    add(make_table([
        ['Submission item','Location'],
        ['First app (modified)','hw5/service1/app.py'],
        ['Second app (unchanged from HW4)','hw5/service2/main.py'],
        ['Stop function','hw5/sql_monitor/main.py'],
        ['Database schema','hw5/schema.sql and hw5/setup_schema.py'],
        ['Automated deployment/cleanup','hw5/setup.sh; hw5/startup.sh; hw5/cleanup.sh'],
        ['Load test, statistics, timing scripts','hw5/run_load.sh; hw5/request_statistics.py; hw5/analyze_timings.py'],
        ['Saved curl logs','hw5/evidence/curl_*.txt'],
        ['Load-run outputs','hw5/evidence/load_*/request_statistics.json; timings_run.csv'],
        ['Cloud billing addendum','billing_repord.pdf (once cloud billing data is final)'],
    ],[205,WIDTH-205]))
    add(par('<b>Repository:</b> <link href="https://github.com/zukhriddingit/cs528-hw4-vm-services/tree/main/hw5" color="#195EA8">github.com/zukhriddingit/cs528-hw4-vm-services/tree/main/hw5</link>. Verify that the HW5 files have actually been committed and pushed to the default branch before submitting this link.','Smallx'))
    add(par('Note: the dataset is synthetic course traffic and should not be interpreted as actual users\' nationalities, ages, incomes, or IP locations.','Fine'))

    screenshots_dir=EVIDENCE/'screenshots'
    pics=sorted([*screenshots_dir.glob('*.png'),*screenshots_dir.glob('*.jpg'),*screenshots_dir.glob('*.jpeg')]) if screenshots_dir.exists() else []
    if pics:
        add(PageBreak());add(heading('Appendix A. Original curl terminal screenshots'))
        for path in pics:
            add(par(esc(path.name),'H2x'))
            try:
                from reportlab.lib.utils import ImageReader
                from PIL import Image as PILImage
                with PILImage.open(path) as im:
                    w,h=im.size
                factor=min(WIDTH/w, 580/h)
                add(Image(str(path),width=w*factor,height=h*factor))
                add(Spacer(1,8))
            except Exception as exc:
                add(par(f'Could not embed screenshot: {esc(exc)}','Smallx'))
    doc.build(elems)
    print(f'Report written: {output}')
    print(f'Run mode: {"PILOT DRAFT" if draft else "FULL"}; evidence directory: {run_dir}')
    if not pics:
        print('Reminder: add real curl screenshots to hw5/evidence/screenshots/ then regenerate.')
    if cost_usd is None:
        print('Reminder: real billing total not yet provided; submit billing_repord.pdf separately if necessary.')


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run-dir',type=Path,help='Completed load experiment directory')
    cli.add_argument('--output',type=Path,default=None,help='Output report PDF path')
    cli.add_argument('--cost-usd',type=float,help='Total confirmed HW5 GCP spend (USD)')
    args=cli.parse_args()
    d=select_run(args.run_dir)
    if d is None:
        stats=PILOT
        timings, measured_n=PILOT_TIMINGS,200
        draft=True
    else:
        stats=json.loads((d/'request_statistics.json').read_text(encoding='utf-8'))
        if '_50000x2' in d.name and not (d/'timings_run.csv').is_file():
            raise SystemExit('Full-run statistics present but timings_run.csv is missing; cannot make final report yet.')
        timings, measured_n,_=get_timing_records(d/'timings_run.csv')
        draft=not ('_50000x2' in d.name and stats.get('rows_matching_course_client_headers')==100000)
    if not draft and measured_n < 100000:
        print('WARNING: Timing evidence is incomplete even if request statistics are complete.')
    output=args.output or ROOT.parent/('CS528_HW5_Report_DRAFT.pdf' if draft else 'CS528_HW5_Report.pdf')
    output.parent.mkdir(parents=True,exist_ok=True)
    report(stats,timings,measured_n,d,draft,output,args.cost_usd)


if __name__=='__main__':
    main()

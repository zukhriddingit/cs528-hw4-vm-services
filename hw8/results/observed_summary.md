# HW8: observed experimental summary

These observations are from the user's terminal output, not a replacement for the original CSV logs. The raw CSVs remain in the user's local HW8/results/ directory; they should be committed before submission.

**Project:** quick-formula-457319-q3. **Load-balancer IP during the experiment:** 34.63.229.117:8080.

- Backends: hw8-server-a (us-central1-a) and hw8-server-b (us-central1-b), both e2-micro/Debian 12, port 8080, attached through zonal unmanaged instance groups.
- LB: Regional external passthrough Network Load Balancer (TCP) in us-central1; HTTP health check /health on port 8080, 5-second interval, 2-second timeout, healthy/unhealthy thresholds 2.
- Baseline, 2026-11-11 02:08:42-02:09:11 UTC, 30 GET requests for /0.html: 15 A and 15 B, zero errors (50%/50%).
- Failover stop: first systemctl stop completed at **2026-11-11 02:11:20.938 UTC**. The second stop at 02:11:56.189 UTC was redundant.
- Failover client, beginning 02:11:06.548 UTC, 90 GET requests for /0.html: original client summary 5 A / 69 B / 16 errors. **Two of the 69 reported B responses had body-read timeouts despite receiving HTTP 200 headers.** Reclassified complete-response results: 5 A, 67 B, and 18 failed or incomplete requests.
- First connection-refused error: 02:11:21.553 UTC; last connection-refused error: **02:11:40.552 UTC**, 19.614 seconds after stop. Next successful B response started 02:11:41.551 UTC. Other timeouts continued, including one starting **02:12:28.772 UTC**, approximately 67.834 seconds after stop. Do not claim all failures ceased within 60 seconds.
- Subsequent GCP health showed A UNHEALTHY, B HEALTHY. HTTP /health requests directly to B and via LB returned 200.
- Recovery start: systemctl start completed **2026-11-11 02:20:43.901 UTC**. First A response after start: **02:21:08.632 UTC**. Client-observed recovery interval: **24.731 seconds**.
- Recovery client: 75 GET /health requests: 60 B, 15 A, zero errors. Among the 28 requests beginning with the first A response, 15 A and 13 B (53.6% / 46.4%). Final GCP health reported both backends HEALTHY.

**Interpretation:** 19.614 seconds is a useful estimate of the end of connection-refused routing errors, not the precise GCP health-state transition. Later timeouts may be client read timeouts, network behavior or backend performance; the cause is not established. The recovery interval is the first observed A response, not exact internal health-check timing.

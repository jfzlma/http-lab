# Usage analytics

HTTP Lab can record one privacy-preserving event per request, so you can see
which endpoints people use, with which clients, and how often things fail,
without storing anything that identifies them.

Analytics is **off by default**. This guide covers what gets recorded, how to
turn it on, and how to query the events on Google Cloud Run (or anywhere else
that collects stdout).

## What an event contains

Each request writes one JSON line to stdout (logger `httplab.analytics`):

```json
{"event":"http_request","severity":"INFO","ts":"2026-10-13T17:27:39+00:00","route":"/status/{code}","method":"GET","status":503,"status_class":"5xx","duration_ms":2.0,"client":"curl","visitor":"1cdc4f42328008ce","referrer":null,"country":null,"ui":false}
```

| Field | Meaning |
|---|---|
| `event` | Always `http_request`. Use it to filter analytics lines from other logs. |
| `ts` | Event time (UTC, whole seconds). |
| `route` | The matched route **template**, e.g. `/status/{code}`; `unmatched` for 404s. Never the raw path or query string. |
| `method`, `status`, `status_class` | HTTP method, response status, and `2xx`/`3xx`/`4xx`/`5xx`. |
| `duration_ms` | Server-side handling time. Includes deliberate delays from `/delay/*` and `delay=`. |
| `client` | A coarse client family from the User-Agent (list below). The User-Agent itself is not stored. |
| `visitor` | A 16-hex-character keyed hash of the client IP that **changes every UTC day**. |
| `referrer` | The referring **host** only (e.g. `github.com`); `null` when absent or same-site. |
| `country` | Two-letter country code, only if a trusted upstream header provides one (see below). |
| `ui` | `true` for the web UI page and the requests it makes; `false` for API clients. |

Client families, matched in this order (the first match wins):

- **HTTP libraries and tools:** `scrapy`, `python-requests`, `httpx`, `aiohttp`,
  `python-urllib`, `curl`, `wget`, `httpie`, `postman`, `insomnia`,
  `go-http-client`, `node`, `okhttp`, `java`, `powershell`
- **Automated traffic:** `search-bot`, `ai-crawler`, `monitoring`, `headless`, `bot-other`
- **Browsers:** `edge`, `opera`, `firefox`, `chrome`, `safari`
- `none` (no User-Agent) and `other` (unrecognised)

**Not recorded:** `/healthz`, `/readyz`, `/favicon.ico`, `/robots.txt` and
anything under `/static/`. These are infrastructure noise, not usage.

Requests rejected before routing (413 for oversized bodies, 429 from the global
rate limit) are still recorded with their route template, so you can see which
endpoints get throttled.

## Privacy

The event never contains IP addresses, raw paths, query strings, request or
response bodies, cookies, `Authorization` headers or any other header values.
The test suite (`tests/test_analytics.py`) sends a request with a token in the
query string, a bearer token and a cookie, and fails if any of them appear in
the event.

The `visitor` hash works like this:

- It is `HMAC-SHA256(ANALYTICS_SECRET, "<UTC date>|<client IP>")`, truncated.
- The same person gets the same id for one UTC day, so you can count **distinct
  visitors per day**.
- The next day they get an unrelated id, so visits cannot be linked across days.
- Without the secret, the id cannot be turned back into an IP, even by
  guessing likely addresses.

Because ids rotate daily, counting distinct `visitor` values over a week
gives *visitor-days*, not people. The queries below are written with that in
mind.

If you run a public instance, say on the site that you collect aggregate usage
statistics. A sentence in the UI or README is enough.

## Turning it on

| Variable | Purpose |
|---|---|
| `ANALYTICS_ENABLED` | `true` to emit events. |
| `ANALYTICS_SECRET` | Key for the visitor hash. Set a long random value in production: `python -c "import secrets; print(secrets.token_hex(32))"`. If unset, a random key is generated at startup, so daily visitor counts restart whenever the service does (a warning is logged). |
| `ANALYTICS_COUNTRY_HEADER` | Optional name of a header that carries an ISO country code, set by something you trust upstream. Leave empty otherwise. |

On Cloud Run, store `ANALYTICS_SECRET` in Secret Manager rather than as a plain
environment variable.

**Also set `LOG_REQUESTS=false` in production.** The regular request log
includes client IPs and is meant for debugging. With analytics on, it is no
longer needed and would undo the privacy guarantees above.

### Country data

HTTP Lab never looks up IPs in a geolocation database or sends them to a third
party, so `country` is only filled when an upstream proxy supplies it:

- **Cloudflare proxy (orange cloud):** set `ANALYTICS_COUNTRY_HEADER=CF-IPCountry`.
  But proxying through Cloudflare also changes what the inspection endpoints
  report (extra headers, Cloudflare's IP as the peer). For an inspection tool,
  "DNS only" is usually the better trade, at the cost of no country data.
- **Plain Cloud Run:** no country header is added, so `country` stays `null`.

Only use a header that your proxy *overwrites* on every request. Otherwise
clients can set it themselves and fake their country.

## Viewing events locally

Events are plain JSON lines on stdout, so `jq` works:

```bash
ANALYTICS_ENABLED=true LOG_REQUESTS=false uv run uvicorn app.main:app --no-access-log \
  | jq -c 'select(.event == "http_request") | {route, status, client}'
```

## Querying on Cloud Run

Cloud Run sends stdout to Cloud Logging and parses each JSON line into the
entry's `jsonPayload`. It also lifts `severity` into the entry's own severity
field. No extra setup is needed for the events to arrive.

### 1. Enable Log Analytics

In the Cloud Console, go to **Logging → Logs Storage**, pick the `_Default`
bucket and choose **Upgrade to use Log Analytics**. This lets you run SQL over
your logs. Optionally, create a linked BigQuery dataset to connect dashboards
such as Looker Studio.

To check events are arriving, use this Logs Explorer filter:

```
resource.type="cloud_run_revision"
jsonPayload.event="http_request"
```

### 2. Keep events longer (optional)

The `_Default` bucket keeps logs for 30 days. To keep analytics longer without
keeping everything else, create a dedicated bucket and route only these events
to it:

```bash
gcloud logging buckets create http-lab-analytics \
  --location=global --retention-days=365 --enable-analytics

gcloud logging sinks create http-lab-analytics \
  logging.googleapis.com/projects/PROJECT_ID/locations/global/buckets/http-lab-analytics \
  --log-filter='resource.type="cloud_run_revision" AND jsonPayload.event="http_request"'
```

Then replace `_Default` with `http-lab-analytics` in the queries below.

### 3. Queries

Run these in **Logging → Log Analytics**. Replace `PROJECT_ID`. Each query
covers the last 30 days; change the `INTERVAL` to widen or narrow it.

**Requests and visitors per day**

```sql
SELECT
  DATE(timestamp) AS day,
  COUNT(*) AS requests,
  COUNT(DISTINCT JSON_VALUE(json_payload.visitor)) AS visitors
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY day
ORDER BY day DESC
```

**Most used endpoints**

```sql
SELECT
  JSON_VALUE(json_payload.route) AS route,
  COUNT(*) AS requests,
  COUNT(DISTINCT CONCAT(CAST(DATE(timestamp) AS STRING), JSON_VALUE(json_payload.visitor))) AS visitor_days
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY route
ORDER BY requests DESC
```

**Which clients people use**

```sql
SELECT
  JSON_VALUE(json_payload.client) AS client,
  COUNT(*) AS requests,
  ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY client
ORDER BY requests DESC
```

**Web UI vs. API clients**

```sql
SELECT
  IF(BOOL(json_payload.ui), 'web UI', 'API client') AS source,
  COUNT(*) AS requests
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY source
```

**Where visitors come from (referring sites)**

```sql
SELECT
  JSON_VALUE(json_payload.referrer) AS referrer,
  COUNT(*) AS requests
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND JSON_VALUE(json_payload.referrer) IS NOT NULL
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY referrer
ORDER BY requests DESC
LIMIT 20
```

**Latency by endpoint.** Simulator delays are included, so a high p95 on
`/delay/{ms}` is expected.

```sql
SELECT
  JSON_VALUE(json_payload.route) AS route,
  COUNT(*) AS requests,
  APPROX_QUANTILES(CAST(JSON_VALUE(json_payload.duration_ms) AS FLOAT64), 100)[OFFSET(50)] AS p50_ms,
  APPROX_QUANTILES(CAST(JSON_VALUE(json_payload.duration_ms) AS FLOAT64), 100)[OFFSET(95)] AS p95_ms
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY route
ORDER BY p95_ms DESC
```

**Throttling and real errors.** Simulated statuses (`/status/500`, `/flaky`)
are excluded, so what remains is the lab's own 413, 429, 503 and 500 responses.

```sql
SELECT
  JSON_VALUE(json_payload.route) AS route,
  CAST(JSON_VALUE(json_payload.status) AS INT64) AS status,
  COUNT(*) AS requests
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND JSON_VALUE(json_payload.status) IN ('413', '429', '500', '503')
  AND JSON_VALUE(json_payload.route) NOT IN ('/status/{code}', '/flaky', '/response', '/content/{fmt}', '/response-size/{size}', '/rate-limit')
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY route, status
ORDER BY requests DESC
```

`/rate-limit` is excluded because its 429s are the simulation working as
intended. To see whether the *global* limit is throttling real users, look at
429s on any other route.

**Countries** (only when `ANALYTICS_COUNTRY_HEADER` is set)

```sql
SELECT
  JSON_VALUE(json_payload.country) AS country,
  COUNT(DISTINCT CONCAT(CAST(DATE(timestamp) AS STRING), JSON_VALUE(json_payload.visitor))) AS visitor_days
FROM `PROJECT_ID.global._Default._AllLogs`
WHERE JSON_VALUE(json_payload.event) = 'http_request'
  AND JSON_VALUE(json_payload.country) IS NOT NULL
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY country
ORDER BY visitor_days DESC
```

### 4. Dashboard (optional)

With the linked BigQuery dataset from step 1, open
[Looker Studio](https://lookerstudio.google.com), add a **BigQuery** data
source, choose **Custom query**, and paste any query above. Each query becomes
a chart. Requests per day and the client mix make a good first dashboard.

## Cost

Each event is roughly 300 bytes. Cloud Logging includes a monthly free
ingestion allowance (50 GiB per project at the time of writing), which covers
well over 100 million events. Check current pricing before relying on that
for a busy public instance.

# AGL Report Fetcher – hosting notes (for the tech team)

Internal web tool. A user drags in an Excel file; the server submits each
Report # + Password to https://www.aglthai.com/report_check.php (same as the
site's "Report Check" form), downloads the returned report image, and hands back
one ZIP: `AGL_Reports.zip` → `<StockID>/<Report#>.jpg` plus `needs_manual_check.csv`.

Excel layout (row 1 = headers): Stock ID = col M, Report # = col AO,
Password = col DV (text like `PASS 0998` is fine – digits are extracted).

## Run it

Docker (recommended):
    docker build -t agl-fetcher .
    docker run -d --restart unless-stopped -p 8000:8000 \
      -e APP_USER=team -e APP_PASSWORD='choose-a-strong-one' agl-fetcher
    -> http://<server>:8000

Without Docker:
    pip install -r requirements.txt
    gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:8000 app:app

## Requirements / decisions for IT

- Outbound HTTPS to www.aglthai.com must be allowed from the server.
- Set APP_USER / APP_PASSWORD (or put it behind your SSO / VPN / reverse proxy).
  Without them anyone who can reach the URL can use it. Do not expose it publicly.
- Put it behind HTTPS (reverse proxy such as nginx/Caddy) – the uploaded sheet contains report passwords.
- Keep `-w 1` (single worker): job progress is held in memory. For multi-worker, move JOBS to Redis.
- Only one batch talks to AGL at a time, with LOOKUP_DELAY (default 2s) between lookups, to avoid being rate-limited/blocked.
- The uploaded Excel is deleted as soon as processing ends; results are deleted after JOB_TTL_MINUTES (default 60).
- Optional env vars: COL_STOCK, COL_REPORT, COL_PASSWORD, LOOKUP_DELAY, MAX_UPLOAD_MB, MAX_ROWS, JOB_TTL_MINUTES.

## Known limits

- If AGL adds a captcha or changes the form/field names (`agl_number`, `password`, `type=1`)
  or the `upload/` image path, `fetch_report_image()` in app.py is the only place to change.
- The business should confirm AGL's terms permit automated lookups.

# Certificate Fetcher — deploy & test (v3)

Multi-lab batch tool. Supports AGL, IGI, HRD and GIA (last two are scaffolded).

## What changed in v3

- **Parallel lookups.** Default `CONCURRENCY=4` — roughly 4× faster than v2.
- **Retries on transient errors.** A dropped connection or a 5xx no longer kills the row; it retries up to 3 times with exponential backoff.
- **Central per-request timeout.** `REQUEST_TIMEOUT=20` seconds. A stuck AGL request fails fast and the retry logic takes over instead of stalling the batch.
- **Partial downloads.** Every 25 rows the server rebuilds a running ZIP. If the batch wedges or errors out, you get a **"Download partial"** button with whatever finished.
- **Stall detection.** If the server stops making progress for >45s the UI tells you so instead of silently pretending to work.
- **Longer Gunicorn timeout** (300s) and more threads (16) so a slow upload or a burst of status polls doesn't kill a worker.
- **MAX_ROWS raised** 1000 → 2000.

## Push the update

1. Unzip this package over your local `agl-fetcher` folder (overwrite).
2. On GitHub.com → **Diacrown/Certificate-Scrapper** → **Add file** → **Upload files**. Drag everything in, including the whole `adapters/` folder. Commit with `Upgrade to v3 (concurrent + retrying)`.
3. Fly will see the push and redeploy. Watch Activity on https://fly.io/apps/certificate-scrapper for the new release.
4. First deploy of v3 may take 2-3 minutes because the Dockerfile's gunicorn flags changed.

Sanity check after redeploy: the home page should show **"Concurrency: 4"** in the status bar. If you still see the old UI with no concurrency pill, GitHub's upload didn't overwrite `templates/index.html` — try again.

## Tuning for your workload

All of these live in `fly.toml`'s `[env]` block. Change there, commit, Fly auto-redeploys.

| Env var | Default | What to raise it to | What to lower it to |
|---|---|---|---|
| `CONCURRENCY` | 4 | 6-8 if AGL is clearly keeping up and you want faster batches | 2 if you start seeing a lot of 429/503 or timeouts (AGL is rate-limiting you) |
| `LOOKUP_DELAY` | 0.3 | 1-2 if AGL complains | 0 if you're sure they don't |
| `REQUEST_TIMEOUT` | 20 | 40-60 if AGL's server is genuinely slow | — |
| `RETRIES` | 3 | 5 if you're seeing a lot of transient failures | 1 if you want faster fail on bad credentials |
| `MAX_ROWS` | 2000 | 5000 if needed | — |

Rule of thumb: start with defaults. Only tune if `fly logs -a certificate-scrapper` shows a pattern (timeouts clustered → raise `REQUEST_TIMEOUT`; 429s → lower `CONCURRENCY`).

## Memory

Default VM is `shared-cpu-1x` / 1024 MB. Each worker holds one certificate in memory while writing it, then drops it. At CONCURRENCY=4 with ~500 KB images, peak RAM from work is ~2 MB — Python + openpyxl + Flask baseline is ~100 MB. You have headroom. If you go to `CONCURRENCY=16` with multi-MB PDFs, consider bumping to 2 GB.

## Testing an untested lab (IGI, HRD once wired)

Every tab has a **"Test a single report"** section — enter one certificate number, click Test, get the file straight to your computer. Use this *before* a 500-row batch.

### IGI
Paste one real IGI number (any form: `694592561`, `LG455033464`, `#694592561`). If it downloads a PDF: 🎉 — edit `adapters/igi.py` on GitHub, change `tested = False` to `tested = True`, commit. The IGI tab's status pill turns green.

### HRD
Still unavailable by default. See `adapters/hrd.py` for the hook-up checklist (needs My HRD Antwerp credentials in `HRD_USER` / `HRD_PASS` fly secrets, plus you'll need to capture the login/download URLs from DevTools).

### GIA
Unavailable — reCAPTCHA on the public form. See `adapters/gia.py` for the two viable paths (GIA Client API or headless + solver).

## Secrets (one-time)

```
fly secrets set APP_USER=team APP_PASSWORD='strong-one' -a certificate-scrapper
# Only when you wire up HRD or GIA:
fly secrets set HRD_USER=... HRD_PASS=... -a certificate-scrapper
fly secrets set GIA_API_KEY=... -a certificate-scrapper
```

## Recovering a wedged batch

If a batch stalls or the UI shows **"No progress for Xs"**:

1. Click **Download partial (whatever finished)**. You get a ZIP of every row that already succeeded, plus the running `needs_manual_check.csv`.
2. Diff those stock IDs against your Excel, make a shorter sheet with only the rows that didn't make it, and re-upload.
3. If the same rows always fail, check `fly logs -a certificate-scrapper` to see why (often: AGL side timed out on those report numbers specifically).

## Excel column layout

| What | Column (default) | Env var to override |
|---|---|---|
| Stock ID | M | `COL_STOCK` |
| AGL Report # | AO | `AGL_COL_CERT` |
| AGL Password | DV | `AGL_COL_PASSWORD` |
| IGI Report # | AO | `IGI_COL_CERT` |
| HRD Report # | AO | `HRD_COL_CERT` |
| GIA Report # | AO | `GIA_COL_CERT` |

## Day-to-day

- Live logs: `fly logs -a certificate-scrapper`
- Status: `fly status -a certificate-scrapper`
- Redeploy: push to GitHub (auto-redeploys)

## Safety rails

- Uploaded Excel deleted immediately after processing.
- Results auto-deleted after `JOB_TTL_MINUTES` (default 60).
- Single Gunicorn worker — don't change. Job state is in-memory; a second worker would see empty state.
- Dockerfile uses 16 threads, so concurrent uploads AND concurrent status polls coexist fine.

## Adding a fifth lab

1. Copy `adapters/agl.py` to `adapters/<newlab>.py`, change class + key + URLs.
2. Add `from .<newlab> import <NewLab>Adapter` to `adapters/__init__.py` and include it in `REGISTRY`.
3. Commit, Fly redeploys, new tab shows up automatically.

"""Certificate Fetcher - multi-lab version (v3, concurrent + retrying).

Upload an Excel file + pick a lab (AGL / IGI / HRD / GIA) -> the server looks
up each report in parallel and returns one ZIP with a folder per stock ID
containing its certificate image or PDF.

Config via environment variables (all optional):
  APP_USER / APP_PASSWORD   enable login (HTTP Basic auth). Recommended.
  COL_STOCK                 Excel column for stock ID (default M)
  <LAB>_COL_CERT            per-lab override for cert-number column
  <LAB>_COL_PASSWORD        per-lab override for password column
  CONCURRENCY               parallel lookups per batch (default 4)
  LOOKUP_DELAY              seconds between lookups PER WORKER (default 0.3)
  REQUEST_TIMEOUT           per-HTTP-call timeout seconds (default 20)
  RETRIES                   transient-error retries per lookup (default 3)
  MAX_UPLOAD_MB             default 10
  JOB_TTL_MINUTES           how long results stay downloadable (default 60)
  MAX_ROWS                  default 2000
  HRD_USER / HRD_PASS       HRD My HRD Antwerp credentials (for HRD adapter)
  GIA_API_KEY               GIA Client API key (for GIA adapter)
"""
import csv, io, os, re, shutil, tempfile, threading, time, uuid, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from pathlib import Path
from typing import Optional

import openpyxl
import requests
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from openpyxl.utils import column_index_from_string as col

from adapters import REGISTRY, LabAdapter, LabError, LabNotConfigured

# ---------- config ----------
COL_STOCK = col(os.getenv("COL_STOCK", "M"))
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "4")))
DELAY = float(os.getenv("LOOKUP_DELAY", "0.3"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "20"))
RETRIES = max(0, int(os.getenv("RETRIES", "3")))
TTL = int(os.getenv("JOB_TTL_MINUTES", "60")) * 60
MAX_ROWS = int(os.getenv("MAX_ROWS", "2000"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ---------- auth ----------
def auth_required(f):
    user, pw = os.getenv("APP_USER"), os.getenv("APP_PASSWORD")

    @wraps(f)
    def wrapper(*a, **kw):
        if not pw:
            return f(*a, **kw)
        c = request.authorization
        if not c or c.username != (user or "") or c.password != pw:
            return Response("Login required", 401,
                            {"WWW-Authenticate": 'Basic realm="Certificate Fetcher"'})
        return f(*a, **kw)
    return wrapper


# ---------- helpers ----------
def _col_for(lab_key: str, which: str, default: Optional[str]) -> Optional[int]:
    env_name = f"{lab_key.upper()}_COL_{which.upper()}"
    v = os.getenv(env_name) or default
    return col(v) if v else None


def safe_name(v) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(v).strip())


def parse_password(v) -> Optional[str]:
    """AGL-style: extract digits from text like 'PASS 0998'."""
    if v is None:
        return None
    m = re.search(r"(\d+)", str(v))
    return m.group(1) if m else None


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (internal certificate fetcher)"
    # Timeout is applied per-call in the adapters via s.request; we also use a
    # low-level socket timeout on the session as a safety net.
    s.request = _timeout_wrapper(s.request, REQUEST_TIMEOUT)  # type: ignore[assignment]
    return s


def _timeout_wrapper(fn, default_timeout):
    """Apply a default timeout to every session.request call unless one is set."""
    def wrapped(method, url, **kw):
        kw.setdefault("timeout", default_timeout)
        return fn(method, url, **kw)
    return wrapped


def read_rows(path: str, lab: LabAdapter) -> list[tuple]:
    """Return [(excel_row_no, stock, cert_no, raw_password), ...] from the sheet."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    cert_col = _col_for(lab.key, "CERT", lab.default_cert_col)
    pw_col = _col_for(lab.key, "PASSWORD", lab.default_password_col) if lab.needs_password else None

    out = []
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        g = lambda c: row[c - 1] if c and len(row) >= c else None
        stock = g(COL_STOCK)
        cert = g(cert_col)
        pw = g(pw_col)
        if stock is None and cert is None:
            continue
        out.append((n, stock, cert, pw))
    return out


# ---------- retry wrapper ----------
# Error classes that are worth retrying (transient / networky).
_TRANSIENT_EXC = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _is_transient_http(e: Exception) -> bool:
    if isinstance(e, requests.HTTPError):
        resp = getattr(e, "response", None)
        if resp is not None and 500 <= resp.status_code < 600:
            return True
    return False


def _fetch_with_retry(lab: LabAdapter, session, cert, pw, retries: int):
    """Call lab.fetch with retries on transient failures. Returns FetchResult or raises."""
    last = None
    for attempt in range(retries + 1):
        try:
            return lab.fetch(session, cert, pw)
        except LabNotConfigured:
            raise  # not worth retrying; surfaces to batch runner
        except LabError:
            raise  # per-row logical failure (wrong password, no image) — don't retry
        except Exception as e:
            last = e
            if attempt >= retries or not (isinstance(e, _TRANSIENT_EXC) or _is_transient_http(e)):
                raise
            # Exponential backoff: 1s, 2s, 4s, ...
            time.sleep(min(2 ** attempt, 10))
    raise last  # unreachable, but keeps type-checkers happy


# ---------- background job ----------
def _process_one(job: dict, lab: LabAdapter, work: Path, row: tuple, session_local):
    """Worker-side: process one row. Returns ('ok', msg) or ('bad', (row_data)) or raises for fatal."""
    n, stock, cert, pw_raw = row
    sid = safe_name(stock) if stock else None
    cert_str = str(cert).strip() if cert is not None else None
    pw = parse_password(pw_raw) if lab.needs_password else None
    label = sid or f"row {n}"

    reason = None
    if not sid:
        reason = "missing stock ID"
    elif not cert_str:
        reason = "missing certificate #"
    elif lab.needs_password and not pw:
        reason = "missing password"
    if reason:
        return ("bad", (n, sid or "", cert_str or "", reason), f"✗ {label}: {reason}")

    # Each worker thread has its own session; create on first use.
    if session_local.session is None:
        session_local.session = new_session()
    s = session_local.session

    try:
        res = _fetch_with_retry(lab, s, cert_str, pw, RETRIES)
    except LabNotConfigured:
        raise
    except Exception as e:
        return ("bad", (n, sid, cert_str, str(e)), f"✗ {sid}: {e}")

    # Write to disk straight away so memory stays flat even for big batches.
    d = work / sid
    d.mkdir(exist_ok=True)
    (d / f"{cert_str}{res.extension}").write_bytes(res.content)
    return ("ok", None, f"✓ {sid}: saved {cert_str}{res.extension}")


def _build_zip(work: Path, zpath: Path) -> None:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in work.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(work))


def _write_problems_csv(work: Path, problems: list[tuple]) -> None:
    if not problems:
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["excel_row", "stock_id", "certificate_no", "reason"])
    w.writerows(problems)
    (work / "needs_manual_check.csv").write_text(buf.getvalue(), encoding="utf-8-sig")


def run_job(job_id: str, xlsx_path: str, lab_key: str):
    job = JOBS[job_id]
    lab = REGISTRY[lab_key]
    work = Path(job["dir"]) / "out"
    work.mkdir()
    problems: list[tuple] = []
    zpath = Path(job["dir"]) / f"{lab.key.upper()}_Certificates.zip"
    job["zip"] = str(zpath)
    job["zip_name"] = zpath.name

    try:
        rows = read_rows(xlsx_path, lab)
        if len(rows) > MAX_ROWS:
            raise RuntimeError(f"Too many rows ({len(rows)}). Limit is {MAX_ROWS}.")
        job["total"] = len(rows)
        job["state"] = "running"

        # Give each worker thread its own requests.Session — connection pool + cookies
        # stay local, no contention. threading.local() carries session.session per thread.
        thread_state = threading.local()
        thread_state.session = None

        def task(row):
            # Rebind on each thread; threading.local is per-thread.
            if not hasattr(thread_state, "session"):
                thread_state.session = None
            # Gentle pacing per worker, so we don't fire N requests in the same millisecond.
            if DELAY > 0:
                time.sleep(DELAY)
            return _process_one(job, lab, work, row, thread_state)

        with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix=f"fetch-{lab.key}") as ex:
            futures = {ex.submit(task, row): row for row in rows}
            done_count = 0
            for fut in as_completed(futures):
                try:
                    kind, bad, log_line = fut.result()
                except LabNotConfigured as e:
                    raise RuntimeError(f"{lab.display_name}: {e}") from e

                if kind == "ok":
                    job["ok"] += 1
                else:
                    problems.append(bad)
                job["log"].append(log_line)
                done_count += 1
                job["done"] = done_count
                job["heartbeat"] = time.time()

                # Build an interim ZIP every 25 rows so the user can download
                # partial results even if the batch later wedges.
                if done_count % 25 == 0:
                    _write_problems_csv(work, problems)
                    try:
                        _build_zip(work, zpath)
                        job["partial_ready"] = True
                    except Exception:
                        pass  # partial is best-effort, final build is what matters

        _write_problems_csv(work, problems)
        _build_zip(work, zpath)

        job["problems"] = len(problems)
        job["state"] = "finished"

    except Exception as e:
        # On fatal error, still try to build a ZIP of whatever succeeded so the
        # user can download the partial result instead of losing everything.
        job["error"] = str(e)
        try:
            _write_problems_csv(work, problems)
            _build_zip(work, zpath)
            job["partial_ready"] = True
        except Exception:
            pass
        job["state"] = "error"
        job["problems"] = len(problems)
    finally:
        try:
            os.remove(xlsx_path)
        except OSError:
            pass


def cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        with JOBS_LOCK:
            stale = [j for j, v in JOBS.items() if now - v["created"] > TTL]
            for jid in stale:
                shutil.rmtree(JOBS[jid]["dir"], ignore_errors=True)
                del JOBS[jid]


threading.Thread(target=cleanup_loop, daemon=True).start()


# ---------- routes ----------
@app.get("/")
@auth_required
def index():
    labs = [
        {
            "key": a.key,
            "display_name": a.display_name,
            "blurb": a.blurb,
            "needs_password": a.needs_password,
            "tested": a.tested,
            "notes": a.notes,
            "cert_col": os.getenv(f"{a.key.upper()}_COL_CERT") or a.default_cert_col,
            "password_col": (os.getenv(f"{a.key.upper()}_COL_PASSWORD") or a.default_password_col) if a.needs_password else None,
        }
        for a in REGISTRY.values()
    ]
    return render_template(
        "index.html", labs=labs, stock_col=os.getenv("COL_STOCK", "M"),
        concurrency=CONCURRENCY,
    )


@app.post("/upload")
@auth_required
def upload():
    lab_key = (request.form.get("lab") or "").lower()
    if lab_key not in REGISTRY:
        return jsonify(error=f"Unknown lab: {lab_key!r}"), 400

    f = request.files.get("file")
    if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify(error="Please upload an .xlsx file"), 400

    jid = uuid.uuid4().hex
    d = tempfile.mkdtemp(prefix=f"cf_{jid}_")
    xp = os.path.join(d, "input.xlsx")
    f.save(xp)

    with JOBS_LOCK:
        JOBS[jid] = dict(
            dir=d, created=time.time(), heartbeat=time.time(),
            state="starting", lab=lab_key,
            total=0, done=0, ok=0, problems=0,
            log=[], zip=None, zip_name=None, error=None,
            partial_ready=False,
        )
    threading.Thread(target=run_job, args=(jid, xp, lab_key), daemon=True).start()
    return jsonify(job=jid)


@app.get("/status/<jid>")
@auth_required
def status(jid):
    j = JOBS.get(jid) or abort(404)
    keys = ("state", "total", "done", "ok", "problems", "error", "lab",
            "zip_name", "partial_ready", "heartbeat")
    base = {k: j.get(k) for k in keys}
    return jsonify(base | {"log": j["log"][-200:]})


@app.get("/download/<jid>")
@auth_required
def download(jid):
    """Download the full ZIP once the job is finished."""
    j = JOBS.get(jid) or abort(404)
    if j["state"] != "finished":
        abort(409)
    return send_file(j["zip"], as_attachment=True, download_name=j["zip_name"])


@app.get("/download-partial/<jid>")
@auth_required
def download_partial(jid):
    """Download whatever has been built so far, even if the batch is still running
    or errored out. Useful recovery if something wedges mid-batch."""
    j = JOBS.get(jid) or abort(404)
    if not j.get("partial_ready") or not j.get("zip"):
        abort(409)
    name = (j.get("zip_name") or "Certificates.zip").replace(".zip", "_partial.zip")
    return send_file(j["zip"], as_attachment=True, download_name=name)


# ---------- single-cert test ----------
@app.post("/test")
@auth_required
def test_single():
    lab_key = (request.form.get("lab") or "").lower()
    if lab_key not in REGISTRY:
        return jsonify(error=f"Unknown lab: {lab_key!r}"), 400
    lab = REGISTRY[lab_key]

    cert = (request.form.get("cert") or "").strip()
    if not cert:
        return jsonify(error="Missing certificate number"), 400

    raw_pw = request.form.get("password")
    pw = parse_password(raw_pw) if lab.needs_password else None
    if lab.needs_password and not pw:
        return jsonify(error=f"{lab.display_name} requires a password"), 400

    s = new_session()
    try:
        res = _fetch_with_retry(lab, s, cert, pw, RETRIES)
    except LabNotConfigured as e:
        return jsonify(error=f"{lab.display_name} not configured: {e}"), 501
    except (LabError, requests.RequestException) as e:
        return jsonify(error=str(e)), 502

    return send_file(
        io.BytesIO(res.content),
        as_attachment=True,
        download_name=f"{safe_name(cert)}{res.extension}",
        mimetype="application/pdf" if res.extension == ".pdf" else "image/jpeg",
    )


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

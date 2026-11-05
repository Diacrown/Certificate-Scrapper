"""
AGL Report Fetcher - small internal web app.

Upload an Excel file -> the server looks up each report on aglthai.com
(report # + password, exactly like the "Report Check" form), and returns
one ZIP containing a folder per stock ID with the report image inside.

Config via environment variables (all optional):
  APP_USER / APP_PASSWORD   enable a simple login (HTTP Basic auth). Strongly recommended.
  COL_STOCK, COL_REPORT, COL_PASSWORD   Excel columns (default M, AO, DV)
  LOOKUP_DELAY              seconds between AGL lookups (default 2)
  MAX_UPLOAD_MB             default 10
  JOB_TTL_MINUTES           how long results stay downloadable (default 60)
"""
import csv, io, os, re, shutil, tempfile, threading, time, uuid, zipfile
from functools import wraps
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import openpyxl
import requests
from flask import Flask, Response, abort, jsonify, render_template, request, send_file
from openpyxl.utils import column_index_from_string as col

BASE = "https://www.aglthai.com/"
FORM_URL = urljoin(BASE, "report_check.php")

COL_STOCK = col(os.getenv("COL_STOCK", "M"))
COL_REPORT = col(os.getenv("COL_REPORT", "AO"))
COL_PASSWORD = col(os.getenv("COL_PASSWORD", "DV"))
DELAY = float(os.getenv("LOOKUP_DELAY", "2"))
TTL = int(os.getenv("JOB_TTL_MINUTES", "60")) * 60
MAX_ROWS = int(os.getenv("MAX_ROWS", "1000"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024

JOBS = {}                       # job_id -> dict
JOBS_LOCK = threading.Lock()
AGL_LOCK = threading.Semaphore(1)   # only one batch talks to AGL at a time (be polite)


# ---------- auth (optional) ----------
def auth_required(f):
    user, pw = os.getenv("APP_USER"), os.getenv("APP_PASSWORD")

    @wraps(f)
    def wrapper(*a, **kw):
        if not pw:
            return f(*a, **kw)
        c = request.authorization
        if not c or c.username != (user or "") or c.password != pw:
            return Response("Login required", 401,
                            {"WWW-Authenticate": 'Basic realm="AGL Fetcher"'})
        return f(*a, **kw)
    return wrapper


# ---------- AGL lookup ----------
class ImgFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            s = dict(attrs).get("src")
            if s:
                self.srcs.append(s)


def fetch_report_image(session, report, password):
    r = session.post(FORM_URL, data={"agl_number": report, "password": password, "type": "1"}, timeout=30)
    r.raise_for_status()
    f = ImgFinder()
    f.feed(r.text)
    cands = [s for s in f.srcs if "upload/" in s.lower()]
    if not cands:
        raise RuntimeError("No report image returned (wrong report # or password?)")
    url = urljoin(BASE, cands[0])
    ir = session.get(url, timeout=60)
    ir.raise_for_status()
    if not ir.headers.get("Content-Type", "").startswith("image/"):
        raise RuntimeError("AGL did not return an image")
    ext = Path(url.split("?")[0]).suffix or ".jpg"
    return ir.content, ext


def safe_name(v):
    return re.sub(r'[\\/:*?"<>|]', "_", str(v).strip())


def parse_password(v):
    m = re.search(r"(\d+)", str(v)) if v is not None else None
    return m.group(1) if m else None


def read_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = []
    for n, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        g = lambda c: row[c - 1] if len(row) >= c else None
        stock, report, pw = g(COL_STOCK), g(COL_REPORT), g(COL_PASSWORD)
        if stock is None and report is None:
            continue
        rows.append((n, stock, report, pw))
    return rows


# ---------- background job ----------
def run_job(job_id, xlsx_path):
    job = JOBS[job_id]
    work = Path(job["dir"]) / "out"
    work.mkdir()
    problems = []
    try:
        rows = read_rows(xlsx_path)
        if len(rows) > MAX_ROWS:
            raise RuntimeError(f"Too many rows ({len(rows)}). Limit is {MAX_ROWS}.")
        job["total"] = len(rows)
        job["state"] = "waiting"
        with AGL_LOCK:
            job["state"] = "running"
            s = requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0 (internal report fetcher)"
            for i, (n, stock, report, pw_raw) in enumerate(rows, start=1):
                sid = safe_name(stock) if stock else None
                rep = str(report).strip() if report is not None else None
                pw = parse_password(pw_raw)
                label = sid or f"row {n}"
                reason = None
                if not sid:
                    reason = "missing stock ID"
                elif not rep:
                    reason = "missing report #"
                elif not pw:
                    reason = "missing password"
                if reason:
                    problems.append((n, sid or "", rep or "", reason))
                    job["log"].append(f"✗ {label}: {reason}")
                else:
                    try:
                        data, ext = fetch_report_image(s, rep, pw)
                        d = work / sid
                        d.mkdir(exist_ok=True)
                        (d / f"{rep}{ext}").write_bytes(data)
                        job["ok"] += 1
                        job["log"].append(f"✓ {sid}: saved {rep}{ext}")
                    except Exception as e:
                        problems.append((n, sid, rep, str(e)))
                        job["log"].append(f"✗ {sid}: {e}")
                    time.sleep(DELAY)
                job["done"] = i
        # needs-manual-check report
        if problems:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["excel_row", "stock_id", "report_no", "reason"])
            w.writerows(problems)
            (work / "needs_manual_check.csv").write_text(buf.getvalue(), encoding="utf-8-sig")
        zpath = Path(job["dir"]) / "AGL_Reports.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in work.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(work))
        job["problems"] = len(problems)
        job["zip"] = str(zpath)
        job["state"] = "finished"
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
    finally:
        try:
            os.remove(xlsx_path)   # don't keep the spreadsheet (it contains passwords)
        except OSError:
            pass


def cleanup_loop():
    while True:
        time.sleep(300)
        now = time.time()
        with JOBS_LOCK:
            for jid in [j for j, v in JOBS.items() if now - v["created"] > TTL]:
                shutil.rmtree(JOBS[jid]["dir"], ignore_errors=True)
                del JOBS[jid]


threading.Thread(target=cleanup_loop, daemon=True).start()


# ---------- routes ----------
@app.get("/")
@auth_required
def index():
    return render_template("index.html")


@app.post("/upload")
@auth_required
def upload():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify(error="Please upload an .xlsx file"), 400
    jid = uuid.uuid4().hex
    d = tempfile.mkdtemp(prefix=f"agl_{jid}_")
    xp = os.path.join(d, "input.xlsx")
    f.save(xp)
    with JOBS_LOCK:
        JOBS[jid] = dict(dir=d, created=time.time(), state="starting", total=0, done=0,
                         ok=0, problems=0, log=[], zip=None, error=None)
    threading.Thread(target=run_job, args=(jid, xp), daemon=True).start()
    return jsonify(job=jid)


@app.get("/status/<jid>")
@auth_required
def status(jid):
    j = JOBS.get(jid) or abort(404)
    return jsonify({k: j[k] for k in ("state", "total", "done", "ok", "problems", "error")}
                   | {"log": j["log"][-200:]})


@app.get("/download/<jid>")
@auth_required
def download(jid):
    j = JOBS.get(jid) or abort(404)
    if j["state"] != "finished":
        abort(409)
    return send_file(j["zip"], as_attachment=True, download_name="AGL_Reports.zip")


@app.get("/health")
def health():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

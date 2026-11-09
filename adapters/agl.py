"""AGL (Thailand) — www.aglthai.com

Scrapes the public "Report Check" form. Verified working against aglthai.com
with real report numbers; this is the implementation the earlier single-lab
version shipped with.
"""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from typing import Optional

from .base import LabAdapter, FetchResult, LabError

BASE = "https://www.aglthai.com/"
FORM_URL = urljoin(BASE, "report_check.php")


class _ImgFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.srcs = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            s = dict(attrs).get("src")
            if s:
                self.srcs.append(s)


class AGLAdapter(LabAdapter):
    key = "agl"
    display_name = "AGL (Thailand)"
    blurb = "Asian Gemmological Laboratory — Bangkok. Returns the full report image."
    needs_password = True
    default_cert_col = "AO"
    default_password_col = "DV"
    tested = True
    notes = "Verified against aglthai.com. Password is the digits in the AGL PASS field."

    def fetch(self, session, cert_no: str, password: Optional[str] = None) -> FetchResult:
        if not password:
            raise LabError("AGL requires a password")

        # No explicit timeout -- session wrapper applies REQUEST_TIMEOUT so a
        # stuck AGL request fails fast and the retry logic takes over.
        r = session.post(
            FORM_URL,
            data={"agl_number": cert_no, "password": password, "type": "1"},
        )
        r.raise_for_status()

        finder = _ImgFinder()
        finder.feed(r.text)
        cands = [s for s in finder.srcs if "upload/" in s.lower()]
        if not cands:
            raise LabError("No report image returned (wrong report # or password?)")

        url = urljoin(BASE, cands[0])
        ir = session.get(url)
        ir.raise_for_status()
        if not ir.headers.get("Content-Type", "").startswith("image/"):
            raise LabError("AGL did not return an image")

        ext = Path(url.split("?")[0]).suffix or ".jpg"
        return FetchResult(ir.content, ext)

"""IGI (International Gemological Institute)

IGI exposes a direct PDF endpoint at pdf.igi.org for every valid report number.
No form POST, no CAPTCHA, no login. Verified with publicly shared report numbers:

    https://pdf.igi.org/ID694592561.pdf       (natural diamond, 2025)
    https://pdf.igi.org/ID455033464.pdf       (lab-grown, 2021)

For lab-grown reports whose human-visible number begins with "LG"
(e.g. "LG455033464"), the URL uses only the digits (ID455033464).
We handle that by stripping common prefixes.

This adapter does not need a password. If your Excel sheet has a password
column, leave it blank for IGI rows.

TEST BEFORE PRODUCTION: Run one of your own IGI report numbers through the
"Test a single report" button on the home page and confirm the downloaded
PDF opens and matches the physical certificate. If IGI changes the URL
pattern this is the only place to fix it.
"""
from pathlib import Path
from typing import Optional

from .base import LabAdapter, FetchResult, LabError

# Prefixes that appear on human-readable numbers but NOT in the pdf.igi.org URL.
# Extend if you find new ones.
STRIP_PREFIXES = ("LG", "lg", "ID", "id", "#")


def _normalize(cert_no: str) -> str:
    v = str(cert_no).strip()
    for p in STRIP_PREFIXES:
        if v.startswith(p):
            v = v[len(p):]
            break
    # Keep only alphanumerics -- defends against stray spaces / punctuation.
    return "".join(ch for ch in v if ch.isalnum())


class IGIAdapter(LabAdapter):
    key = "igi"
    display_name = "IGI (International)"
    blurb = "International Gemological Institute. Returns the full certificate PDF."
    needs_password = False
    default_cert_col = "AO"
    default_password_col = None
    tested = False  # flip to True once you've run a real IGI number through it
    notes = (
        "Uses pdf.igi.org direct-download pattern. For 'LG' lab-grown numbers "
        "the prefix is stripped automatically. Test one report first."
    )

    PDF_URL_TEMPLATE = "https://pdf.igi.org/ID{num}.pdf"

    def fetch(self, session, cert_no: str, password: Optional[str] = None) -> FetchResult:
        num = _normalize(cert_no)
        if not num:
            raise LabError("Empty IGI report number")

        url = self.PDF_URL_TEMPLATE.format(num=num)
        # Timeout applied centrally via the session wrapper; retries handled
        # by the batch runner's _fetch_with_retry.
        r = session.get(url)

        if r.status_code == 404:
            raise LabError(f"IGI report {cert_no} not found at {url}")
        r.raise_for_status()

        # IGI serves the PDF with application/pdf. Reject anything else so a
        # redirect-to-HTML page doesn't get saved as a bogus "PDF".
        ctype = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ctype and not r.content.startswith(b"%PDF"):
            raise LabError(
                f"IGI did not return a PDF for {cert_no} "
                f"(content-type: {ctype!r}). The URL pattern may have changed."
            )

        return FetchResult(r.content, ".pdf")

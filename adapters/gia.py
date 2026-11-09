"""GIA (Gemological Institute of America)

GIA's public Report Check (https://www.gia.edu/report-check) is protected by
Google reCAPTCHA v2. A plain form POST will not work. There are two viable
paths, both out of scope for the stock Flask app:

  A) GIA Report API (gia.edu/gia-report-check-api)
     - Requires a GIA Client account and a signed API agreement.
     - Returns JSON report data; the actual eReport PDF is a separate
       authenticated request.
     - Best option if you or DIAMORE/DIAMANTRA already have (or can get)
       GIA Client status.

  B) Headless browser + CAPTCHA solving service (2Captcha, AntiCaptcha, etc.)
     - Needs Playwright or Selenium in the Docker image, plus a paid solver.
     - Costs ~$3 per 1000 CAPTCHAs; also slower (10-30s per report).
     - Technically against GIA's terms; use only if you've got written OK.

Until one of those is wired up this adapter raises LabNotConfigured so rows
headed for GIA fail fast with a clear message instead of silently breaking.

To implement path A: set GIA_API_KEY as a fly secret, then replace fetch()
below with the API call per GIA's client documentation.
"""
import os
from typing import Optional

from .base import LabAdapter, FetchResult, LabError, LabNotConfigured


class GIAAdapter(LabAdapter):
    key = "gia"
    display_name = "GIA (USA)"
    blurb = "Gemological Institute of America. Blocked by reCAPTCHA on the public form."
    needs_password = False
    default_cert_col = "AO"
    default_password_col = None
    tested = False
    notes = (
        "Unavailable without GIA Client API access (or a CAPTCHA solver). "
        "See adapters/gia.py for the two options."
    )

    def fetch(self, session, cert_no: str, password: Optional[str] = None) -> FetchResult:
        api_key = os.getenv("GIA_API_KEY")
        if not api_key:
            raise LabNotConfigured(
                "GIA fetching needs either a GIA Client API key (preferred) or "
                "a headless browser + CAPTCHA solver. Neither is set. "
                "See adapters/gia.py for setup options."
            )
        # TODO: implement GIA Client API call here once GIA_API_KEY is set.
        raise LabNotConfigured(
            "GIA_API_KEY is set but the GIA adapter is not yet implemented. "
            "Replace this body with the GIA Client API call for the PDF eReport."
        )

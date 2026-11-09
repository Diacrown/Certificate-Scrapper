"""HRD Antwerp — hrdantwerp.com

HRD gates PDF downloads behind the "My HRD Antwerp" account (my.hrdantwerp.com).
There is a public verify page (verify.hrdantwerp.com) that confirms a report
number is valid, but it does NOT expose the PDF.

This adapter is scaffolded but not implemented. To turn it on:

  1. Sign in to https://my.hrdantwerp.com in a browser and open DevTools.
  2. In the Network tab, click on an existing report's PDF download.
  3. Capture: the exact URL, any auth headers/cookies, and the response type.
  4. Decide which approach fits:
     a) Static session cookie:
        - Get HRD login credentials into env vars (HRD_USER / HRD_PASS).
        - Implement _login() below to POST to the login endpoint and keep
          session.cookies populated.
        - Implement fetch() below to GET the PDF URL with those cookies.
     b) API token:
        - Ask HRD for API access (they have one for members).
        - Put the token in env var HRD_API_TOKEN.
        - Implement fetch() below to GET with that token in the header.
  5. Flip `tested = True` only after you've downloaded 2-3 real reports
     through this adapter end-to-end.
"""
import os
from typing import Optional

from .base import LabAdapter, FetchResult, LabError, LabNotConfigured


class HRDAdapter(LabAdapter):
    key = "hrd"
    display_name = "HRD Antwerp"
    blurb = "Hoge Raad voor Diamant. Requires HRD account credentials."
    needs_password = False  # the report number itself; HRD auth is separate (env vars)
    default_cert_col = "AO"
    default_password_col = None
    tested = False
    notes = (
        "Not yet implemented. Needs HRD My HRD Antwerp credentials. "
        "See adapters/hrd.py for the hook-up checklist."
    )

    # When credentials are configured, the implementation will cache the
    # authenticated session on the adapter instance.
    _logged_in = False

    def _login(self, session) -> None:
        """Populate `session.cookies` with HRD auth cookies.

        See the module docstring for how to figure out the login POST details.
        """
        user = os.getenv("HRD_USER")
        pw = os.getenv("HRD_PASS")
        if not user or not pw:
            raise LabNotConfigured(
                "HRD needs HRD_USER and HRD_PASS environment variables. "
                "Set them with `fly secrets set HRD_USER=... HRD_PASS=...`."
            )
        # TODO: implement the actual login call once you've captured it.
        # Example shape (DO NOT SHIP AS-IS):
        #
        # r = session.post(
        #     "https://my.hrdantwerp.com/api/auth/login",
        #     json={"email": user, "password": pw},
        #     timeout=30,
        # )
        # r.raise_for_status()
        # self._logged_in = True
        raise LabNotConfigured(
            "HRD login flow not implemented. See adapters/hrd.py comments."
        )

    def fetch(self, session, cert_no: str, password: Optional[str] = None) -> FetchResult:
        if not self._logged_in:
            self._login(session)

        # TODO: implement the actual PDF fetch once you've captured the URL.
        # Example shape (DO NOT SHIP AS-IS):
        #
        # url = f"https://my.hrdantwerp.com/api/reports/{cert_no}/pdf"
        # r = session.get(url, timeout=60)
        # r.raise_for_status()
        # return FetchResult(r.content, ".pdf")
        raise LabNotConfigured(
            "HRD fetch not implemented. See adapters/hrd.py comments."
        )

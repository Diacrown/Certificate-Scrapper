"""Base types shared by all lab adapters."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class FetchResult:
    """Binary content + its file extension (including the dot)."""
    content: bytes
    extension: str  # ".jpg", ".pdf", ".png"


class LabError(Exception):
    """A per-row failure. The batch continues; row goes in needs_manual_check.csv."""


class LabNotConfigured(LabError):
    """Lab is listed but not usable (e.g. needs credentials, blocked by CAPTCHA).

    Raising this stops the whole batch early with a clear message, instead of
    marking every row as failed.
    """


class LabAdapter:
    # URL-safe slug used in forms, URLs, and config env vars. Lowercase.
    key: str = "base"
    # Human-readable name shown in the UI.
    display_name: str = "Base"
    # Short description shown under the lab in the UI picker.
    blurb: str = ""
    # Does this lab require a password / PIN in addition to the report number?
    needs_password: bool = False
    # Default Excel columns if the user doesn't override via env vars.
    default_cert_col: str = "A"
    default_password_col: Optional[str] = None
    # True once we've verified against the live service with a real report.
    # Keep this honest: if you haven't tested it, leave it False.
    tested: bool = False
    # Shown in the UI next to the lab. Use for caveats / status.
    notes: str = ""

    def fetch(self, session, cert_no: str, password: Optional[str] = None) -> FetchResult:
        """Fetch one report. Return FetchResult or raise LabError / LabNotConfigured."""
        raise NotImplementedError

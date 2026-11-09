"""Lab adapters for certificate fetching.

Add a new lab by dropping a module in this folder that exposes a subclass of
LabAdapter, then register it below.
"""
from .base import LabAdapter, LabError, LabNotConfigured, FetchResult
from .agl import AGLAdapter
from .igi import IGIAdapter
from .hrd import HRDAdapter
from .gia import GIAAdapter

# Order matters: this is the order shown in the UI.
REGISTRY: dict[str, LabAdapter] = {
    a.key: a for a in [
        AGLAdapter(),
        IGIAdapter(),
        HRDAdapter(),
        GIAAdapter(),
    ]
}

__all__ = [
    "REGISTRY", "LabAdapter", "LabError", "LabNotConfigured", "FetchResult",
    "AGLAdapter", "IGIAdapter", "HRDAdapter", "GIAAdapter",
]

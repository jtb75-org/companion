"""Per-source ingestion adapters.

Each adapter encapsulates one source's fetch/parse/metadata and declares its
identity + purge policy; the reconcile spine is entirely source-agnostic.
"""

from app.ingestion.adapters.ecfr import ECFRAdapter
from app.ingestion.adapters.fedreg import FederalRegisterAdapter

__all__ = ["ECFRAdapter", "FederalRegisterAdapter"]

# Registry consumed by the worker CLI + admin trigger (--source <key>).
ADAPTERS = {
    "ecfr": ECFRAdapter,
    "fedreg": FederalRegisterAdapter,
}

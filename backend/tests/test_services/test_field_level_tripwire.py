"""CI tripwire — data-minimization / field-level-encryption guard.

Companion deliberately does NOT store SSNs, full bank-account numbers, or
medical record numbers (MRNs). If such a field is ever introduced into a
pipeline schema or model, it MUST be routed through
``field_crypto.encrypt_field_level`` (the dedicated per-field-type key, §7).

This test fails if a sensitive-looking field name appears in the scanned files
without a nearby reference to ``encrypt_field_level`` — forcing an explicit,
reviewed decision rather than silently persisting high-sensitivity PII.
"""

from __future__ import annotations

import re
from pathlib import Path

# Files whose field definitions are in scope for the guard.
_BACKEND = Path(__file__).resolve().parents[2]
_SCANNED = [
    _BACKEND / "app" / "pipeline" / "schemas.py",
    _BACKEND / "app" / "models" / "document.py",
    _BACKEND / "app" / "models" / "user.py",
    _BACKEND / "app" / "models" / "pending_review.py",
    _BACKEND / "app" / "models" / "functional_memory.py",
]

# A "field" is an assignment / annotation whose name matches one of these.
_SENSITIVE = re.compile(
    r"\b("
    r"ssn"
    r"|social_security(?:_number)?"
    r"|full_account_number"
    r"|mrn"
    r"|medical_record(?:_number)?"
    r")\b",
    re.IGNORECASE,
)

# Pattern that looks like a field declaration, e.g. `ssn: str` or `ssn =`.
_FIELD_DECL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]")


def test_no_sensitive_field_without_field_level_encryption():
    offenders: list[str] = []
    for path in _SCANNED:
        if not path.exists():
            continue
        text = path.read_text()
        guarded = "encrypt_field_level" in text
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue  # comments / prose don't define fields
            m = _FIELD_DECL.match(line)
            if not m:
                continue
            name = m.group(1)
            if _SENSITIVE.search(name) and not guarded:
                offenders.append(f"{path.name}:{lineno}: {stripped}")

    assert not offenders, (
        "Sensitive field(s) found without routing through "
        "field_crypto.encrypt_field_level (data-minimization tripwire):\n  "
        + "\n  ".join(offenders)
    )


def test_field_level_capability_exists():
    """The capability the tripwire points at must actually exist."""
    from app.services import field_crypto

    assert hasattr(field_crypto, "encrypt_field_level")
    assert hasattr(field_crypto, "decrypt_field_level")

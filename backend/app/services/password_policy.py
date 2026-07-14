"""App-side password-strength policy for the branded set-password seams.

The Authentik admin ``set_password`` API bypasses Authentik's own flow password
policy, so on ``/invitations/set-password`` and ``/activation/set-password`` this
module is the strength gate. The design is NIST 800-63B-aligned and accessibility-
first for the D.D. member population: length + screening, NO forced composition
(no "must have an uppercase/symbol" — those hurt this audience and NIST advises
against them).

Rules are checked in order and the FIRST violation's plain, warm message is
raised. The rejected password is NEVER included in a message or logged.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings

# Common-password denylist, loaded ONCE at import into a frozenset for O(1) lookup.
# Source: the classic real-world most-common-password lists (rockyou / SecLists top
# passwords). It is a FLOOR to catch the obvious weak passwords, not exhaustive.
_DENYLIST_PATH = Path(__file__).resolve().parent.parent / "data" / "common_passwords.txt"


def _load_denylist(path: Path) -> frozenset[str]:
    entries: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            word = line.strip()
            if word and not word.startswith("#"):
                entries.add(word.lower())
    return frozenset(entries)


COMMON_PASSWORDS: frozenset[str] = _load_denylist(_DENYLIST_PATH)


class PasswordPolicyError(Exception):
    """Raised when a password fails the strength policy.

    ``message`` is the plain, member-safe, user-facing string to surface (never
    contains the rejected password)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _is_predictable(password: str) -> bool:
    """True if the password is a single repeated character or a straight
    ascending/descending run of consecutive code points over its whole length
    (e.g. "0000000000", "1234567890", "abcdefghij", "9876543210").

    Case-normalized first, so mixed-case dressings of a sequence ("Abcdefghij",
    "aBcDeFgHiJ", "Zyxwvutsrq") are caught too — they're no less predictable."""
    if len(password) < 2:
        return True
    p = password.lower()
    # All the same character.
    if len(set(p)) == 1:
        return True
    # Whole-length monotonic run of consecutive characters (asc or desc).
    diffs = {ord(b) - ord(a) for a, b in zip(p, p[1:], strict=False)}
    return diffs in ({1}, {-1})


def validate_password(password: str, *, email: str | None = None) -> None:
    """Validate ``password`` against the strength policy.

    Raises ``PasswordPolicyError`` (with a plain user-facing ``.message``) on the
    FIRST failing rule; returns None when the password passes. Pure/synchronous —
    no I/O beyond the module-loaded denylist. ``settings.password_min_length`` is
    read at call time so it stays tunable/testable."""
    min_length = settings.password_min_length

    # 1. Too short.
    if len(password) < min_length:
        raise PasswordPolicyError(
            f"Please use a longer password — at least {min_length} characters."
        )

    lowered = password.lower()

    # 2. Too common.
    if lowered in COMMON_PASSWORDS:
        raise PasswordPolicyError(
            "That password is too common. Please pick something harder to guess."
        )

    # 3. Too predictable (single repeated char or a straight sequential run).
    if _is_predictable(password):
        raise PasswordPolicyError(
            "Please don't use a simple pattern, like 1234 or 1111."
        )

    # 4. Contains their email's local-part.
    if email:
        local_part = email.split("@", 1)[0].strip().lower()
        if len(local_part) >= 4 and local_part in lowered:
            raise PasswordPolicyError(
                "Please don't use your email in your password."
            )

"""Unit tests for the branded set-password strength policy.

Pure/synchronous: ``validate_password`` raises ``PasswordPolicyError`` on the first
failing rule (too-short → too-common → too-predictable → email-in-password) and
returns None for strong passwords. ``settings.password_min_length`` is honored at
call time (monkeypatched here).
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.password_policy import (
    COMMON_PASSWORDS,
    PasswordPolicyError,
    validate_password,
)


def test_denylist_loaded_and_contains_known_common():
    assert len(COMMON_PASSWORDS) > 300
    assert "password" in COMMON_PASSWORDS
    assert "123456" in COMMON_PASSWORDS
    assert "qwerty" in COMMON_PASSWORDS
    # Stored lowercased.
    assert all(w == w.lower() for w in COMMON_PASSWORDS)


def test_too_short_message_interpolates_min_length(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 10)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password("short1")
    assert ei.value.message == "Please use a longer password — at least 10 characters."


def test_min_length_is_honored_when_tuned(monkeypatch):
    # A 12-char strong password is fine at 10 but rejected when the floor is 16.
    pw = "meadow-lake-42"
    monkeypatch.setattr(settings, "password_min_length", 10)
    validate_password(pw)  # ok
    monkeypatch.setattr(settings, "password_min_length", 16)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password(pw)
    assert "16 characters" in ei.value.message


def test_too_common(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 6)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password("password")
    assert ei.value.message == (
        "That password is too common. Please pick something harder to guess."
    )


def test_too_common_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 6)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password("PassWord")
    assert "too common" in ei.value.message


@pytest.mark.parametrize(
    "pw",
    [
        "0000000000",  # single repeated char
        "aaaaaaaaaa",
        "0123456789",  # ascending run
        "abcdefghij",
        "9876543210",  # descending run
        "jihgfedcba",
    ],
)
def test_too_predictable(monkeypatch, pw):
    monkeypatch.setattr(settings, "password_min_length", 10)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password(pw)
    assert ei.value.message == "Please don't use a simple pattern, like 1234 or 1111."


def test_email_local_part_in_password(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 6)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password("xxjsmithxx99", email="jsmith@example.com")
    assert ei.value.message == "Please don't use your email in your password."


def test_short_email_local_part_is_ignored(monkeypatch):
    # local-part < 4 chars is not screened.
    monkeypatch.setattr(settings, "password_min_length", 6)
    validate_password("abcdefgh-quiet", email="joe@example.com")


def test_email_not_required(monkeypatch):
    monkeypatch.setattr(settings, "password_min_length", 6)
    validate_password("quiet-brook-77")  # no email arg, passes


@pytest.mark.parametrize(
    "pw",
    [
        "sunny-meadow-lake-42",
        "quiet-brook-morning",
        "purpleTiger9284x",
        "correcthorsebatterystaple",
        "n3ver-guess-this-one",
    ],
)
def test_valid_strong_passwords_pass(monkeypatch, pw):
    monkeypatch.setattr(settings, "password_min_length", 10)
    assert validate_password(pw, email="member@mydailydignity.com") is None


def test_rule_order_short_before_common(monkeypatch):
    # "password" is common AND (at floor 10) too short → short wins (first rule).
    monkeypatch.setattr(settings, "password_min_length", 10)
    with pytest.raises(PasswordPolicyError) as ei:
        validate_password("password")
    assert "longer password" in ei.value.message

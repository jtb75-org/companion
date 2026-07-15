from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class ActivationSetPassword(BaseModel):
    """Body for POST /activation/set-password.

    A dedicated schema (not the invitation SetPasswordRequest) so the generic
    activation surface stays decoupled from the caregiver-invitation surface even
    though the shape is currently identical.
    """

    token: str = Field(description="Activation token from the email link")
    # Length is enforced by app.services.password_policy.validate_password at the
    # endpoint (the single length gate, tunable via settings.password_min_length),
    # so NO min_length here — a too-short password returns the plain 422 policy
    # message rather than a 422 schema-validation error. max_length caps abuse.
    # SecretStr so a rejected value is masked in the default 422 body / any repr —
    # the secret is never echoed back. Read at use with .get_secret_value().
    password: SecretStr = Field(max_length=512)

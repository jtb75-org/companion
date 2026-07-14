from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class ActivationSetPassword(BaseModel):
    """Body for POST /activation/set-password.

    A dedicated schema (not the invitation SetPasswordRequest) so the generic
    activation surface stays decoupled from the caregiver-invitation surface even
    though the shape is currently identical.
    """

    token: str = Field(description="Activation token from the email link")
    # 8 is the enforced floor: the Authentik admin set_password API bypasses the flow
    # password policy, so this schema is the only password-strength gate today.
    # SecretStr so a rejected value is masked in the default 422 body / any repr — the
    # secret is never echoed back. Read at use with .get_secret_value().
    password: SecretStr = Field(min_length=8, max_length=512)

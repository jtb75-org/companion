from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import AccessTier, RelationshipType


class InvitationCreate(BaseModel):
    email: str = Field(description="Caregiver's email address")
    contact_name: str = Field(description="Caregiver's full name")
    relationship_type: RelationshipType = Field(description="Relationship to the member")
    access_tier: AccessTier = Field(default=AccessTier.TIER_1, description="Access tier level")


class InvitationAccept(BaseModel):
    token: str = Field(description="Invitation token from the email link")


class SetPasswordRequest(BaseModel):
    token: str = Field(description="Invitation token from the email link")
    # Length is enforced by app.services.password_policy.validate_password at the
    # endpoint (the single length gate, tunable via settings.password_min_length),
    # so NO min_length here — a too-short password returns the plain 422 policy
    # message rather than a 422 schema-validation error. max_length caps abuse.
    # SecretStr so a rejected value is masked in the default 422 body / any repr —
    # the secret is never echoed back. Read at use with .get_secret_value().
    password: SecretStr = Field(max_length=512)


class InvitationResponse(BaseModel):
    contact_id: UUID
    invitation_status: str
    email_sent: bool


class AdminPlatformInvite(BaseModel):
    email: str = Field(description="Invitee's email address")
    name: str = Field(description="Invitee's full name")
    phone: str | None = Field(default=None, description="Phone number")


class AdminPlatformInviteResponse(BaseModel):
    user_id: UUID
    account_status: str
    email_sent: bool
    already_existed: bool


class AssignmentRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    member_id: UUID
    caregiver_email: str
    caregiver_name: str
    relationship_type: str
    access_tier: str
    status: str
    initiated_by: str
    requested_at: datetime
    expires_at: datetime


class AssignmentApproveResponse(BaseModel):
    approved: bool
    contact_id: UUID


class AssignmentRejectResponse(BaseModel):
    rejected: bool


class CaregiverAssignmentRequestCreate(BaseModel):
    member_id: UUID = Field(description="Member to request assignment to")
    relationship_type: RelationshipType = Field(description="Relationship to the member")
    access_tier: AccessTier = Field(default=AccessTier.TIER_1, description="Requested access tier")

"""Email service using Gmail SMTP (Google Workspace).

Sends transactional emails for:
- Caregiver invitations
- Platform invitations
- Assignment notifications
- Welcome emails after profile completion
- Safety alerts to caregivers
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.branding import (
    BRAND_DOMAIN,
    BRAND_EMAIL_FROM_ADDRESS,
    BRAND_EMAIL_FROM_NAME,
    BRAND_MID,
    BRAND_SHORT,
)
from app.config import settings

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

APP_URL = settings.app_url or f"https://app.{BRAND_DOMAIN}"


def _email_wrapper(content: str) -> str:
    """Wrap email content in the standard branded template."""
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 500px; margin: 0 auto; padding: 32px;">
        <div style="text-align: center; margin-bottom: 24px;">
            <span style="font-size: 36px;">🌟</span>
            <h1 style="color: #2C5F8A; font-size: 22px; margin: 8px 0 0;">{BRAND_MID}</h1>
        </div>
        {content}
        <hr style="border: none; border-top: 1px solid #eee; margin: 24px 0;" />
        <p style="color: #aaa; font-size: 12px; text-align: center;">— The {BRAND_SHORT} Team</p>
    </div>
    """


def _cta_button(url: str, label: str) -> str:
    return f"""
    <div style="text-align: center; margin: 32px 0;">
        <a href="{url}"
           style="background: #2C5F8A; color: white; padding: 14px 32px; border-radius: 12px; text-decoration: none; font-weight: 600; display: inline-block;">
            {label}
        </a>
    </div>
    """


def _send_smtp(to_email: str, to_name: str, subject: str, text_body: str, html_body: str | None) -> bool:
    """Send a single email via Gmail SMTP."""
    if not settings.gmail_smtp_password:
        logger.warning("Gmail SMTP not configured — emails will be logged only")
        logger.info(f"Email (no smtp): to={to_email} subject=\"{subject}\"")
        return True  # Pretend success in dev

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{BRAND_EMAIL_FROM_NAME} <{BRAND_EMAIL_FROM_ADDRESS}>"
    msg["To"] = f"{to_name} <{to_email}>"
    msg["Subject"] = subject

    msg.attach(MIMEText(text_body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(settings.gmail_smtp_user, settings.gmail_smtp_password)
            server.sendmail(BRAND_EMAIL_FROM_ADDRESS, to_email, msg.as_string())
        logger.info(f"Email sent: to={to_email} subject=\"{subject}\"")
        return True
    except Exception:
        logger.exception(f"Failed to send email to {to_email}")
        return False


async def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send a single email via Gmail SMTP."""
    return _send_smtp(to_email, to_name, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Caregiver invitation (member-initiated, includes token link)
# ---------------------------------------------------------------------------

async def send_caregiver_invitation(
    to_email: str,
    to_name: str,
    user_name: str,
    relationship: str,
    invited_by: str,
    invitation_token: str | None = None,
) -> bool:
    """Send a caregiver invitation email with an acceptance link."""
    subject = f"You've been invited to {BRAND_MID}"

    if invitation_token:
        accept_url = f"{APP_URL}/invite/accept?token={invitation_token}"
        cta_text = f"To accept, click the link below and sign in with your Google account.\n\n{accept_url}"
    else:
        accept_url = APP_URL
        cta_text = f"To get started, visit the {BRAND_SHORT} dashboard and sign in with your Google account ({to_email})."

    text_body = (
        f"Hi {to_name},\n\n"
        f"{invited_by} has invited you as a {relationship} "
        f"for {user_name} on {BRAND_MID}.\n\n"
        f"{cta_text}\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p><strong>{invited_by}</strong> has invited you as a <strong>{relationship}</strong> for <strong>{user_name}</strong> on {BRAND_MID}.</p>"
        f"<p>As a caregiver, you'll be able to view {user_name}'s status, upcoming appointments, and important alerts.</p>"
        + _cta_button(accept_url, "Accept Invitation")
        + f'<p style="color: #888; font-size: 13px;">Sign in with your Google account ({to_email}) to get started.</p>'
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Account activation (branded set-password — admin now, members later)
# ---------------------------------------------------------------------------

async def send_activation_email(
    to_email: str,
    to_name: str,
    token: str,
) -> bool:
    """Send an account-activation email with a link to set a password.

    Generic across cohorts (an admin created under Authentik now, members later): the
    token is email-keyed. The CTA lands on the branded /activate page where the person
    chooses their password, then signs in."""
    subject = f"Set up your {BRAND_MID} account"

    activate_url = f"{APP_URL}/activate?token={token}"

    text_body = (
        f"Hi {to_name},\n\n"
        f"An account has been created for you on {BRAND_MID}.\n\n"
        f"To finish setting up, choose a password using the link below:\n\n"
        f"{activate_url}\n\n"
        f"Once your password is set, you can sign in any time.\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p>An account has been created for you on {BRAND_MID}.</p>"
        f"<p>To finish setting up, choose a password. Once it's set, you can sign in any time.</p>"
        + _cta_button(activate_url, "Set your password")
        + '<p style="color: #888; font-size: 13px;">If you weren\'t expecting this, you can safely ignore this email.</p>'
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Platform invitation (admin-initiated, Part 1 only — no member assignment)
# ---------------------------------------------------------------------------

async def send_platform_invitation(
    to_email: str,
    to_name: str,
    invited_by: str,
) -> bool:
    """Send a platform invitation email (no specific member assignment)."""
    subject = f"You've been invited to {BRAND_MID}"

    text_body = (
        f"Hi {to_name},\n\n"
        f"{invited_by} has invited you to join {BRAND_MID}.\n\n"
        f"To get started, visit {APP_URL} and sign in with your Google account ({to_email}).\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p><strong>{invited_by}</strong> has invited you to join {BRAND_MID}.</p>"
        f"<p>{BRAND_SHORT} is an independence assistant that helps adults stay on top of their daily tasks, appointments, and medications.</p>"
        + _cta_button(APP_URL, f"Sign In to {BRAND_SHORT}")
        + f'<p style="color: #888; font-size: 13px;">Sign in with your Google account ({to_email}) to get started.</p>'
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Assignment notifications
# ---------------------------------------------------------------------------

async def send_assignment_request_notification(
    to_email: str,
    to_name: str,
    caregiver_name: str,
    relationship: str,
) -> bool:
    """Notify a member that someone wants to be their caregiver."""
    subject = f"{caregiver_name} would like to be your caregiver"

    text_body = (
        f"Hi {to_name},\n\n"
        f"{caregiver_name} would like to be added as your {relationship} on {BRAND_MID}.\n\n"
        f"Log in to {APP_URL} to approve or decline this request.\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p><strong>{caregiver_name}</strong> would like to be added as your <strong>{relationship}</strong> on {BRAND_MID}.</p>"
        f"<p>You can approve or decline this request from your dashboard.</p>"
        + _cta_button(APP_URL, "Review Request")
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


async def send_assignment_approved_notification(
    to_email: str,
    to_name: str,
    member_name: str,
) -> bool:
    """Notify a caregiver that a member approved their assignment."""
    subject = f"{member_name} approved you as a caregiver"

    text_body = (
        f"Hi {to_name},\n\n"
        f"{member_name} has approved you as a caregiver on {BRAND_MID}.\n\n"
        f"You can now view their dashboard at {APP_URL}.\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p><strong>{member_name}</strong> has approved you as a caregiver on {BRAND_MID}.</p>"
        f"<p>You can now view their dashboard, alerts, and activity.</p>"
        + _cta_button(APP_URL, "View Dashboard")
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


async def send_assignment_rejected_notification(
    to_email: str,
    to_name: str,
    member_name: str,
) -> bool:
    """Notify a caregiver that a member declined their assignment."""
    subject = f"Caregiver request update — {BRAND_SHORT}"

    text_body = (
        f"Hi {to_name},\n\n"
        f"{member_name} has declined the caregiver request on {BRAND_MID}.\n\n"
        f"If you believe this is an error, please contact your administrator.\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    return await send_email(to_email, to_name, subject, text_body)


async def send_invitation_accepted_notification(
    to_email: str,
    to_name: str,
    caregiver_name: str,
) -> bool:
    """Notify a member that their caregiver accepted the invitation."""
    subject = f"{caregiver_name} accepted your invitation"

    text_body = (
        f"Hi {to_name},\n\n"
        f"{caregiver_name} has accepted your invitation and is now your caregiver on {BRAND_MID}.\n\n"
        f"They'll receive updates and alerts on your behalf.\n\n"
        f"— The {BRAND_SHORT} Team"
    )

    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p><strong>{caregiver_name}</strong> has accepted your invitation and is now your caregiver on {BRAND_MID}.</p>"
        f"<p>They'll receive updates and important alerts on your behalf.</p>"
        + _cta_button(APP_URL, f"Open {BRAND_SHORT}")
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Account lifecycle notifications
# ---------------------------------------------------------------------------

async def send_account_deactivated(to_email: str, to_name: str) -> bool:
    """Confirm account deactivation to the user."""
    subject = f"Your {BRAND_SHORT} account has been deactivated"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Your {BRAND_MID} account has been deactivated. "
        f"Your data is safe and your account can be reactivated at any time.\n\n"
        f"While deactivated, caregivers will not receive updates or alerts "
        f"and you will not receive check-ins or reminders.\n\n"
        f"To reactivate, sign in at {APP_URL}.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p>Your {BRAND_MID} account has been <strong>deactivated</strong>.</p>"
        f"<p>Your data is safe and your account can be reactivated at any time. While deactivated:</p>"
        f"<ul><li>Caregivers will not receive updates or alerts</li><li>You will not receive check-ins or reminders</li></ul>"
        + _cta_button(APP_URL, "Reactivate Account")
    )
    return await send_email(to_email, to_name, subject, text_body, html_body)


async def send_account_reactivated(to_email: str, to_name: str) -> bool:
    """Confirm account reactivation to the user."""
    subject = f"Welcome back to {BRAND_SHORT}"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Your {BRAND_MID} account has been reactivated. "
        f"Everything is back to normal — check-ins, reminders, and caregiver access are restored.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p>Your {BRAND_MID} account has been <strong>reactivated</strong>. Welcome back!</p>"
        f"<p>Check-ins, reminders, and caregiver access have been restored.</p>"
        + _cta_button(APP_URL, f"Go to {BRAND_SHORT}")
    )
    return await send_email(to_email, to_name, subject, text_body, html_body)


async def send_deletion_requested(to_email: str, to_name: str, scheduled_date: str) -> bool:
    """Confirm deletion request with the scheduled date."""
    subject = f"Your {BRAND_SHORT} account is scheduled for deletion"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Your {BRAND_MID} account has been scheduled for permanent deletion on {scheduled_date}.\n\n"
        f"After that date, all your data will be permanently removed and cannot be recovered.\n\n"
        f"If you change your mind, sign in at {APP_URL} before {scheduled_date} to cancel.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    html_body = _email_wrapper(
        f"<p>Hi {to_name},</p>"
        f"<p>Your {BRAND_MID} account has been scheduled for <strong>permanent deletion on {scheduled_date}</strong>.</p>"
        f"<p>After that date, all your data will be permanently removed and cannot be recovered.</p>"
        f"<p>If you change your mind, sign in before {scheduled_date} to cancel the deletion.</p>"
        + _cta_button(APP_URL, "Cancel Deletion")
    )
    return await send_email(to_email, to_name, subject, text_body, html_body)


async def send_deletion_cancelled(to_email: str, to_name: str) -> bool:
    """Confirm deletion cancellation."""
    subject = f"Your {BRAND_SHORT} account deletion has been cancelled"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Your {BRAND_MID} account deletion has been cancelled. "
        f"Your account is still deactivated — sign in to reactivate it.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    return await send_email(to_email, to_name, subject, text_body)


async def send_caregiver_access_revoked(
    to_email: str, to_name: str, member_name: str, reason: str
) -> bool:
    """Notify a caregiver that their access has been revoked."""
    subject = f"Your access to {member_name}'s {BRAND_SHORT} has been removed"
    text_body = (
        f"Hi {to_name},\n\n"
        f"Your caregiver access to {member_name}'s {BRAND_MID} account has been removed "
        f"because the account has been {reason}.\n\n"
        f"If you believe this is an error, please contact your administrator.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    return await send_email(to_email, to_name, subject, text_body)


async def send_account_deleted_to_caregiver(
    to_email: str, to_name: str, member_name: str
) -> bool:
    """Notify a caregiver that a member's account was permanently deleted."""
    subject = f"{member_name}'s {BRAND_SHORT} account has been deleted"
    text_body = (
        f"Hi {to_name},\n\n"
        f"{member_name}'s {BRAND_MID} account has been permanently deleted. "
        f"All associated data has been removed.\n\n"
        f"You will no longer receive alerts or have access to their dashboard.\n\n"
        f"— The {BRAND_SHORT} Team"
    )
    return await send_email(to_email, to_name, subject, text_body)


# ---------------------------------------------------------------------------
# Welcome & safety alerts
# ---------------------------------------------------------------------------

async def send_welcome(
    to_email: str,
    to_name: str,
) -> bool:
    """Send a welcome email after profile completion."""
    subject = f"Welcome to {BRAND_MID}"

    text_body = (
        f"Hi {to_name},\n\n"
        f"Welcome to {BRAND_MID}! Your profile is all set.\n\n"
        f"{BRAND_SHORT} is here to help you stay on top of things — "
        f"your mail, appointments, medications, and whatever else you need.\n\n"
        f"— {BRAND_SHORT}"
    )

    return await send_email(to_email, to_name, subject, text_body)


async def send_safety_alert(
    to_email: str,
    to_name: str,
    user_name: str,
    alert_type: str,
    alert_message: str,
) -> bool:
    """Send a safety alert to a caregiver."""
    subject = f"{BRAND_SHORT} Alert: {user_name}"

    text_body = (
        f"Hi {to_name},\n\n"
        f"This is an alert about {user_name}:\n\n"
        f"{alert_message}\n\n"
        f"You may want to check in with {user_name}.\n\n"
        f"— {BRAND_SHORT}"
    )

    html_body = _email_wrapper(
        f'<div style="background: #FDF3E7; border: 1px solid #D4832A33; border-radius: 12px; padding: 16px; margin: 16px 0;">'
        f'<p style="margin: 0; font-weight: 600; color: #D4832A;">⚠️ Alert about {user_name}</p>'
        f'<p style="margin: 8px 0 0; color: #4A4A6A;">{alert_message}</p>'
        f"</div>"
        f"<p>You may want to check in with {user_name}.</p>"
        + _cta_button(APP_URL, "View Dashboard")
    )

    return await send_email(to_email, to_name, subject, text_body, html_body)

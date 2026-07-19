"""Gmail integration — OAuth flow and email ingestion.

Production flow:
1. User initiates Gmail connect via POST /integrations/gmail/connect
2. Server redirects to Google OAuth consent screen
3. Callback receives auth code, exchanges for tokens
4. Background worker polls inbox periodically
5. New emails → document pipeline

This module provides the scaffolding. Full OAuth flow requires:
- A Google Cloud OAuth 2.0 client ID (web application type)
- Authorized redirect URI configured in GCP console
- gmail.readonly scope
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


@dataclass
class GmailConnection:
    """Represents an active Gmail connection for a user."""
    user_id: str
    email: str
    connected: bool = False
    access_token: str | None = None
    refresh_token: str | None = None


async def initiate_oauth(user_id: str, redirect_uri: str) -> str:
    """Generate the Google OAuth consent URL.

    Returns the URL to redirect the user to for Gmail authorization.
    """
    # TODO: Use google-auth-oauthlib to generate the real URL
    # from google_auth_oauthlib.flow import Flow
    # flow = Flow.from_client_config(client_config, scopes=GMAIL_SCOPES)
    # flow.redirect_uri = redirect_uri
    # auth_url, _ = flow.authorization_url(prompt='consent')
    # return auth_url
    #
    # SECURITY (pentest 2026-07-19, currently a non-functional stub so not exploitable):
    # when wiring the real flow, the authorization URL MUST include a signed/opaque `state`
    # param bound to the user's session and validated on the callback (else account-linking
    # CSRF), use an HTTPS `redirect_uri` (no cleartext auth-code hop), and include client_id.

    logger.info(f"Gmail OAuth initiated for user {user_id}")
    return f"https://accounts.google.com/o/oauth2/auth?scope=gmail.readonly&redirect_uri={redirect_uri}&response_type=code"


async def handle_oauth_callback(
    code: str, user_id: str
) -> GmailConnection:
    """Exchange OAuth code for tokens and store connection.

    Returns the established connection.
    """
    # TODO: Exchange code for tokens via google-auth
    logger.info(f"Gmail OAuth callback for user {user_id}")
    return GmailConnection(
        user_id=user_id,
        email="connected@gmail.com",
        connected=True,
    )


async def fetch_new_emails(connection: GmailConnection) -> list[dict]:
    """Fetch unread emails from a connected Gmail account.

    Returns a list of email dicts ready for the document pipeline.
    Each dict has: subject, from, body_text, received_at, attachments.
    """
    # TODO: Use Gmail API to fetch new messages
    # service = build('gmail', 'v1', credentials=credentials)
    # results = service.users().messages().list(
    #     userId='me', q='is:unread'
    # ).execute()

    logger.info(f"Fetching emails for {connection.email}")
    return []


async def disconnect(user_id: str) -> bool:
    """Revoke Gmail access and remove stored tokens."""
    logger.info(f"Gmail disconnected for user {user_id}")
    return True

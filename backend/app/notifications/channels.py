from __future__ import annotations

import logging
from uuid import UUID

from app.events.publisher import event_publisher
from app.events.schemas import NotificationDeliveredPayload

logger = logging.getLogger(__name__)


async def deliver_push(
    user_id: UUID, title: str, body: str, data: dict | None = None
) -> bool:
    """Send a push notification via Firebase Cloud Messaging.

    Stubbed for now — will integrate with Firebase Admin SDK.
    """
    logger.info(
        f"Push notification: user={user_id} "
        f"title=\"{title}\" body=\"{body[:60]}...\""
    )

    # FCM delivery is handled by services/push_notification_service.py (FCM v1 HTTP
    # API via a service-account key + httpx). This channel emits the delivery event.

    await event_publisher.publish(
        "notification.delivered",
        user_id=user_id,
        payload=NotificationDeliveredPayload(
            notification_id=UUID(int=0),
            channel="push",
            user_id=user_id,
            content_type="text",
        ),
    )
    return True


async def deliver_in_app(
    user_id: UUID, title: str, body: str, priority: int = 4
) -> bool:
    """Create an in-app notification card.

    In a full implementation, this writes to a notifications table
    and the mobile app polls or receives via WebSocket.
    """
    logger.info(
        f"In-app notification: user={user_id} "
        f"priority={priority} title=\"{title}\""
    )
    # TODO: Write to notifications table
    return True


async def deliver_voice(
    user_id: UUID, text: str, voice_id: str = "warm"
) -> bool:
    """Queue a voice notification for D.D. to speak.

    Used when the app is open and active.
    """
    logger.info(
        f"Voice notification: user={user_id} "
        f"voice={voice_id} text=\"{text[:60]}...\""
    )
    # TODO: Queue for TTS delivery via conversation layer
    return True

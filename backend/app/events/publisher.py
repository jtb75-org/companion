from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


class EventPublisher:
    """Publishes events to Google Cloud Pub/Sub.

    In development, uses the Pub/Sub emulator.
    Falls back to logging events if Pub/Sub is unavailable.
    """

    def __init__(self):
        self._client = None
        self._topic_cache: dict[str, str] = {}

    def _get_client(self):
        if self._client is None:
            if settings.pubsub_emulator_host:
                os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host
                # Quick connectivity check for emulator
                import socket
                host, port = settings.pubsub_emulator_host.split(":")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                try:
                    sock.connect((host, int(port)))
                    sock.close()
                except OSError:
                    logger.info("Pub/Sub emulator not reachable, using local handlers")
                    return None

            try:
                from google.cloud import pubsub_v1
                self._client = pubsub_v1.PublisherClient()
            except Exception:
                logger.warning("Pub/Sub client unavailable, using local handlers")
                self._client = None
        return self._client

    def _topic_path(self, event_name: str) -> str:
        """Convert event name to Pub/Sub topic path.

        document.processed -> projects/{project}/topics/companion-{env}-document-processed
        """
        if event_name not in self._topic_cache:
            topic_name = f"companion-{settings.environment}-{event_name.replace('.', '-')}"
            self._topic_cache[event_name] = (
                f"projects/{settings.gcp_project_id}/topics/{topic_name}"
            )
        return self._topic_cache[event_name]

    async def publish(
        self,
        event_name: str,
        user_id: UUID,
        payload: BaseModel | dict,
    ) -> str | None:
        """Publish an event to Pub/Sub.

        Returns the message ID if published, None if Pub/Sub unavailable.
        """
        event_id = str(uuid4())

        envelope = {
            "event_id": event_id,
            "event_name": event_name,
            "user_id": str(user_id),
            "occurred_at": datetime.utcnow().isoformat(),
            "payload": (
                payload.model_dump(mode="json")
                if isinstance(payload, BaseModel)
                else payload
            ),
        }

        data = json.dumps(envelope, default=str).encode("utf-8")

        client = self._get_client()
        if client is None:
            logger.info(f"Event (no pubsub): {event_name} user={user_id} id={event_id}")
            # Store in local subscribers for in-process handling
            await self._handle_locally(event_name, envelope)
            return event_id

        try:
            topic_path = self._topic_path(event_name)
            future = client.publish(
                topic_path,
                data,
                event_name=event_name,
                user_id=str(user_id),
            )
            # future.result() is a SYNCHRONOUS blocking call — off-load it so it
            # never freezes the event loop (this runs on the live conversation
            # turn for caregiver.alert.triggered, and a dead/absent topic would
            # otherwise stall every request sharing the loop for up to `timeout`).
            message_id = await asyncio.to_thread(future.result, timeout=3)
            logger.info(f"Event published: {event_name} user={user_id} msg={message_id}")
            return message_id
        except Exception:
            logger.exception(f"Failed to publish event: {event_name}")
            # Fall back to local handling
            await self._handle_locally(event_name, envelope)
            return event_id

    async def _handle_locally(self, event_name: str, envelope: dict):
        """Handle events in-process when Pub/Sub is unavailable."""
        for handler in _local_handlers.get(event_name, []):
            try:
                await handler(envelope)
            except Exception:
                logger.exception(f"Local handler failed for {event_name}")


# Local handler registry for in-process event handling (dev/test)
_local_handlers: dict[str, list] = {}


def on_event(event_name: str):
    """Decorator to register a local event handler."""
    def decorator(func):
        _local_handlers.setdefault(event_name, []).append(func)
        return func
    return decorator


# Singleton publisher
event_publisher = EventPublisher()

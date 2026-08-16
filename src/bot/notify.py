"""Direct-message helpers for Slack assignment notifications."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_dm(client, user_id: str, text: str, blocks: list[dict] | None = None) -> bool:
    """Open (or reuse) a DM with a Slack user and deliver a notification.

    Returns False without raising when the DM cannot be delivered (a bot user, a
    deactivated account, a missing `im:write` scope) so an assignment flow can
    continue and the caller can decide how to surface the failure, instead of the
    whole interaction crashing because one notification could not be sent.
    """
    if not user_id:
        return False
    try:
        opened = await client.conversations_open(users=user_id)
        dm_channel_id = opened["channel"]["id"]
        await client.chat_postMessage(channel=dm_channel_id, text=text, blocks=blocks, mrkdwn=True)
        return True
    except Exception:
        logger.exception("Could not DM user_id=%s", user_id)
        return False

import asyncio

from src.bot.frontend_support_app import BUSY_TEXT, _finish_progress, _start_progress


class FakeClient:
    def __init__(self):
        self.posts = []
        self.updates = []

    async def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ts": "999.1"}

    async def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return {"ok": True}


def test_progress_posts_immediate_acknowledgement_and_replaces_it():
    client = FakeClient()

    ts = asyncio.run(
        _start_progress(client, channel_id="C-FRONT", thread_ts="100.1")
    )
    asyncio.run(
        _finish_progress(
            client,
            channel_id="C-FRONT",
            message_ts=ts,
            text="Final support answer",
        )
    )

    assert ts == "999.1"
    assert client.posts == [
        {
            "channel": "C-FRONT",
            "thread_ts": "100.1",
            "text": BUSY_TEXT,
        }
    ]
    assert client.updates[0]["channel"] == "C-FRONT"
    assert client.updates[0]["ts"] == "999.1"
    assert client.updates[0]["text"] == "Final support answer"
    assert client.updates[0]["blocks"] == []


def test_progress_can_be_replaced_with_block_kit_clarification():
    client = FakeClient()
    blocks = [{"type": "actions", "elements": []}]

    asyncio.run(
        _finish_progress(
            client,
            channel_id="C-FRONT",
            message_ts="999.2",
            text="Which application is affected?",
            blocks=blocks,
        )
    )

    assert client.updates[0]["blocks"] == blocks

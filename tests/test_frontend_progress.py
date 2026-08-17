import asyncio

from src.bot.frontend_support_app import (
    BUSY_TEXT,
    DONE_TEXT,
    WAITING_TEXT,
    _finish_progress,
    _start_progress,
    _with_status,
)


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


def test_private_progress_is_not_forced_into_a_thread():
    client = FakeClient()

    ts = asyncio.run(_start_progress(client, channel_id="D-FRONT"))

    assert ts == "999.1"
    assert client.posts == [{"channel": "D-FRONT", "text": BUSY_TEXT}]


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


def test_status_markers_make_waiting_and_completion_explicit_without_duplication():
    assert _with_status("Which application is affected?", WAITING_TEXT) == (
        "Which application is affected?\n\n" + WAITING_TEXT
    )
    completed = _with_status("Final support answer", DONE_TEXT)
    assert completed == "Final support answer\n\n" + DONE_TEXT
    assert _with_status(completed, DONE_TEXT) == completed

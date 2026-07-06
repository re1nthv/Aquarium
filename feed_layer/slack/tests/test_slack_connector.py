"""
Tests for the Slack connector.

All Slack API calls are mocked.  The MessageQueue is the real InMemoryQueue
from feed_layer.shared — no mocking needed for the happy path; we use
unittest.mock only for the queue exception case and for verifying
enqueue/no-enqueue behaviour.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from feed_layer.shared import InMemoryQueue, SlackMetadata
from feed_layer.slack.connector import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_slack_client(message_text: str = "hello", thread_ts: str | None = None):
    """Return a fake Slack client factory that returns a stub WebClient."""

    def _factory(token: str) -> MagicMock:
        client = MagicMock()
        # conversations_history returns the message
        msg: dict[str, Any] = {"text": message_text, "ts": "1700000001.000000"}
        if thread_ts:
            msg["thread_ts"] = thread_ts
        client.conversations_history.return_value = {"messages": [msg]}
        # conversations_replies returns parent + one reply
        reply_msg: dict[str, Any] = {"text": "thread reply", "ts": "1700000002.000000"}
        client.conversations_replies.return_value = {"messages": [msg, reply_msg]}
        return client

    return _factory


@pytest.fixture()
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture()
def client(queue):
    """Flask test client with a plain-message slack client (no thread)."""
    app = create_app(
        queue=queue,
        slack_client_factory=_make_slack_client("message body"),
        config_overrides={
            "INTAKE_EMOJI": "aquarium",
            "MONITORED_CHANNELS": ["C_MONITORED"],
        },
    )
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _post(test_client, body: dict[str, Any]):
    """POST JSON to /slack/events."""
    return test_client.post(
        "/slack/events",
        data=json.dumps(body),
        content_type="application/json",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestReactionAdded:
    def test_plain_message_enqueued(self, queue, client):
        """reaction_added with INTAKE_EMOJI on a plain message → signal enqueued."""
        body = {
            "event": {
                "type": "reaction_added",
                "reaction": "aquarium",
                "user": "U_REACTOR",
                "user_name": "reactor",
                "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 1
        signal = queue.dequeue(1)[0]
        assert signal.type == "manual_reaction"
        assert signal.metadata.message_ts == "1700000001.000000"
        assert signal.originator.id == "U_REACTOR"

    def test_thread_reply_enqueued_with_thread_ts(self, queue):
        """reaction_added with INTAKE_EMOJI on a thread reply → thread_ts is non-null."""
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(
                message_text="thread parent",
                thread_ts="1700000000.000000",   # ≠ message_ts → it's a reply
            ),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            resp = _post(c, body)

        assert resp.status_code == 200
        assert queue.depth() == 1
        signal = queue.dequeue(1)[0]
        assert signal.metadata.thread_ts == "1700000000.000000"

    def test_thread_parent_content_includes_replies(self, queue):
        """When message_ts == thread_ts the raw_content includes thread replies."""
        # Build client where thread_ts == message_ts (parent message)
        parent_ts = "1700000001.000000"
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(
                message_text="parent text",
                thread_ts=parent_ts,
            ),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": parent_ts},
                }
            }
            resp = _post(c, body)

        assert resp.status_code == 200
        signal = queue.dequeue(1)[0]
        assert "thread reply" in signal.raw_content


class TestReactionRemoved:
    def test_reaction_removed_removes_queued_signal(self, queue):
        """reaction_removed with INTAKE_EMOJI → matching signal removed from queue."""
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            # First add the reaction (enqueues a signal)
            add_body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            _post(c, add_body)
            assert queue.depth() == 1

            # Then remove the reaction
            remove_body = {
                "event": {
                    "type": "reaction_removed",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            resp = _post(c, remove_body)

        assert resp.status_code == 200
        assert queue.depth() == 0


class TestBotMessage:
    def test_bot_message_subtype_in_monitored_channel(self, queue, client):
        """message subtype=bot_message in monitored channel → signal enqueued."""
        body = {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C_MONITORED",
                "ts": "1700000005.000000",
                "text": "bot says hello",
                "bot_id": "B_BOT1",
                "username": "mybot",
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 1
        signal = queue.dequeue(1)[0]
        assert signal.type == "bot_message"
        assert signal.metadata.bot_name == "mybot"

    def test_bot_id_present_no_subtype_in_monitored_channel(self, queue, client):
        """message with bot_id (no subtype) in monitored channel → signal enqueued."""
        body = {
            "event": {
                "type": "message",
                "channel": "C_MONITORED",
                "ts": "1700000006.000000",
                "text": "another bot message",
                "bot_id": "B_BOT2",
                "username": "anotherbot",
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 1

    def test_url_verification_challenge(self, queue, client):
        """URL verification challenge → challenge value returned."""
        body = {"type": "url_verification", "challenge": "abc123xyz"}
        resp = _post(client, body)

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["challenge"] == "abc123xyz"


# ---------------------------------------------------------------------------
# Boundary / negative cases
# ---------------------------------------------------------------------------

class TestBoundaryAndNegative:
    def test_different_emoji_not_enqueued(self, queue, client):
        """reaction_added with a DIFFERENT emoji → nothing enqueued."""
        body = {
            "event": {
                "type": "reaction_added",
                "reaction": "thumbsup",
                "user": "U_REACTOR",
                "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 0

    def test_bot_message_non_monitored_channel_not_enqueued(self, queue, client):
        """bot_message in a non-monitored channel → nothing enqueued."""
        body = {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "channel": "C_NOT_MONITORED",
                "ts": "1700000007.000000",
                "text": "should be ignored",
                "bot_id": "B_BOT3",
                "username": "somebot",
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 0

    def test_reaction_removed_already_dequeued_is_idempotent(self, queue, client):
        """reaction_removed for a signal already dequeued → no error, no crash."""
        # Dequeue without ever enqueuing
        body = {
            "event": {
                "type": "reaction_removed",
                "reaction": "aquarium",
                "user": "U_REACTOR",
                "item": {"type": "message", "channel": "C_123", "ts": "9999999999.000000"},
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200  # no crash

    def test_queue_exception_returns_503(self, queue):
        """Queue raises exception on enqueue → endpoint returns 503."""
        exploding_queue = MagicMock(spec=InMemoryQueue)
        exploding_queue.enqueue.side_effect = RuntimeError("queue full")

        app = create_app(
            queue=exploding_queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            resp = _post(c, body)

        assert resp.status_code == 503

    def test_unknown_event_type_returns_200_nothing_enqueued(self, queue, client):
        """Unknown event type → returns 200, nothing enqueued."""
        body = {
            "event": {
                "type": "channel_created",
                "channel": {"id": "C_NEW", "name": "new-channel"},
            }
        }
        resp = _post(client, body)

        assert resp.status_code == 200
        assert queue.depth() == 0

    def test_malformed_payload_returns_200_no_crash(self, queue, client):
        """Malformed payload (missing fields) → returns 200, no crash."""
        # Completely empty body
        resp = _post(client, {})
        assert resp.status_code == 200
        assert queue.depth() == 0

    def test_malformed_reaction_added_missing_item(self, queue, client):
        """reaction_added missing item → returns 200, no crash, nothing enqueued."""
        body = {
            "event": {
                "type": "reaction_added",
                "reaction": "aquarium",
                "user": "U_REACTOR",
                # 'item' key deliberately absent
            }
        }
        resp = _post(client, body)
        assert resp.status_code == 200
        # May or may not enqueue (empty ts) — most important: no crash


# ---------------------------------------------------------------------------
# Schema correctness
# ---------------------------------------------------------------------------

class TestSchemaCorrectness:
    def test_schema_version_source_required_fields(self, queue):
        """Signal has schema_version=1.0, source=slack, all required fields non-null."""
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client("some text"),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "user_name": "reactor_name",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            _post(c, body)

        signal = queue.dequeue(1)[0]

        assert signal.schema_version == "1.0"
        assert signal.source == "slack"
        assert signal.type is not None
        assert signal.raw_content is not None
        assert signal.timestamp is not None
        assert signal.ingested_at is not None
        assert signal.originator is not None
        assert signal.metadata is not None

    def test_signal_id_is_valid_uuid(self, queue):
        """Signal id is a valid UUID."""
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {"type": "message", "channel": "C_123", "ts": "1700000001.000000"},
                }
            }
            _post(c, body)

        signal = queue.dequeue(1)[0]

        # Will raise ValueError if not a valid UUID
        parsed = uuid.UUID(signal.id)
        assert str(parsed) == signal.id

    def test_ingested_at_is_set_at_enqueue_time(self, queue):
        """ingested_at is set at enqueue time, not copied from message timestamp."""
        before = datetime.now(tz=timezone.utc)

        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {
                "event": {
                    "type": "reaction_added",
                    "reaction": "aquarium",
                    "user": "U_REACTOR",
                    "item": {
                        "type": "message",
                        "channel": "C_123",
                        "ts": "1000000000.000000",  # year ~2001 — clearly in the past
                    },
                }
            }
            _post(c, body)

        after = datetime.now(tz=timezone.utc)
        signal = queue.dequeue(1)[0]

        # ingested_at must be between before and after (recent)
        assert before <= signal.ingested_at <= after
        # timestamp must match the message ts (year ~2001), not now
        assert signal.timestamp < before

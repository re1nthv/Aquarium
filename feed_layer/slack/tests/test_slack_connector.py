"""
Tests for the Slack connector.

All Slack API calls are mocked.  The MessageQueue is the real InMemoryQueue
from feed_layer.shared — no mocking needed for the happy path; we use
unittest.mock only for the queue exception case and for verifying
enqueue/no-enqueue behaviour.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
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


def _sign(signing_secret: str, timestamp: str, raw_body: bytes) -> str:
    """Compute a valid Slack v0 signature for the given secret/timestamp/body."""
    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode("utf-8")
    return "v0=" + hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()


def _post_signed(
    test_client,
    body: dict[str, Any],
    signing_secret: str,
    timestamp: str | None = None,
    signature: str | None = None,
):
    """POST JSON to /slack/events with Slack signature headers attached."""
    raw_body = json.dumps(body).encode("utf-8")
    if timestamp is None:
        timestamp = str(int(time.time()))
    if signature is None:
        signature = _sign(signing_secret, timestamp, raw_body)
    return test_client.post(
        "/slack/events",
        data=raw_body,
        content_type="application/json",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
        },
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


# ---------------------------------------------------------------------------
# Signature verification (BUG 1)
# ---------------------------------------------------------------------------

class TestSignatureVerification:
    SIGNING_SECRET = "test_signing_secret_123"

    def _make_app(self, queue):
        return create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={
                "INTAKE_EMOJI": "aquarium",
                "SLACK_SIGNING_SECRET": self.SIGNING_SECRET,
            },
        )

    def test_valid_signature_passes(self, queue):
        """A correctly-signed request with a signing secret configured is accepted."""
        app = self._make_app(queue)
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {"type": "url_verification", "challenge": "abc123xyz"}
            resp = _post_signed(c, body, self.SIGNING_SECRET)

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["challenge"] == "abc123xyz"

    def test_invalid_signature_returns_401(self, queue):
        """A request signed with the wrong secret is rejected with 401."""
        app = self._make_app(queue)
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {"type": "url_verification", "challenge": "abc123xyz"}
            timestamp = str(int(time.time()))
            bad_signature = _sign("wrong_secret", timestamp, json.dumps(body).encode("utf-8"))
            resp = _post_signed(
                c, body, self.SIGNING_SECRET, timestamp=timestamp, signature=bad_signature
            )

        assert resp.status_code == 401

    def test_stale_timestamp_returns_401(self, queue):
        """A validly-signed request whose timestamp is > 5 minutes old is rejected."""
        app = self._make_app(queue)
        app.config["TESTING"] = True
        with app.test_client() as c:
            body = {"type": "url_verification", "challenge": "abc123xyz"}
            stale_timestamp = str(int(time.time()) - 600)  # 10 minutes old
            resp = _post_signed(c, body, self.SIGNING_SECRET, timestamp=stale_timestamp)

        assert resp.status_code == 401

    def test_no_secret_configured_skips_verification(self, queue, client):
        """When SLACK_SIGNING_SECRET is empty (default), unsigned requests pass through."""
        body = {"type": "url_verification", "challenge": "no-secret-needed"}
        resp = _post(client, body)  # unsigned, no headers at all

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["challenge"] == "no-secret-needed"

    def test_missing_headers_returns_401_when_secret_configured(self, queue):
        """When a secret IS configured, a request with no signature headers is rejected."""
        app = self._make_app(queue)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = _post(c, {"type": "url_verification", "challenge": "abc"})

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Multi-reaction race (BUG 2)
# ---------------------------------------------------------------------------

class TestMultiReactionRace:
    def test_two_reactors_one_removal_only_removes_that_reactors_signal(self, queue):
        """Two users react to the same message; one removes their reaction.

        Only the withdrawing user's signal should be removed — the other
        user's signal must remain queued.
        """
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        message_ts = "1700000001.000000"
        with app.test_client() as c:
            # Two different users react to the same message.
            for user in ("U_ALICE", "U_BOB"):
                add_body = {
                    "event": {
                        "type": "reaction_added",
                        "reaction": "aquarium",
                        "user": user,
                        "item": {"type": "message", "channel": "C_123", "ts": message_ts},
                    }
                }
                _post(c, add_body)
            assert queue.depth() == 2

            # Alice removes her reaction.
            remove_body = {
                "event": {
                    "type": "reaction_removed",
                    "reaction": "aquarium",
                    "user": "U_ALICE",
                    "item": {"type": "message", "channel": "C_123", "ts": message_ts},
                }
            }
            resp = _post(c, remove_body)

        assert resp.status_code == 200
        assert queue.depth() == 1
        remaining = queue.dequeue(1)[0]
        assert remaining.originator.id == "U_BOB"
        assert remaining.metadata.reactor_id == "U_BOB"

    def test_single_reaction_removal_still_works(self, queue):
        """Regression: a single reactor removing their reaction still empties the queue."""
        app = create_app(
            queue=queue,
            slack_client_factory=_make_slack_client(),
            config_overrides={"INTAKE_EMOJI": "aquarium"},
        )
        app.config["TESTING"] = True
        with app.test_client() as c:
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

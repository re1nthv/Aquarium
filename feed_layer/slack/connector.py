"""
Slack connector for the Aquarium feed layer.

Exposes a Flask app with two endpoints:
  POST /slack/events  — receives Slack event callbacks
  GET  /healthz       — liveness probe (optional convenience)

Usage
-----
    from feed_layer.slack.connector import create_app
    from feed_layer.shared import InMemoryQueue

    queue = InMemoryQueue()
    app = create_app(queue)
    app.run(port=3000)

Injectable seams
-----------------
create_app() accepts:
  queue           — any MessageQueue implementation
  slack_client    — callable(token) → object with a conversations_history method
                    defaults to slack_sdk.WebClient; inject a mock in tests
  config_overrides — dict that can override INTAKE_EMOJI, MONITORED_CHANNELS,
                     SLACK_SIGNING_SECRET
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from flask import Flask, Response, jsonify, request

from feed_layer.shared import (
    MessageQueue,
    Originator,
    Signal,
    SlackMetadata,
)
from . import config as _default_config

logger = logging.getLogger(__name__)

# Slack rejects (and we mirror) requests whose timestamp header is older than
# this many seconds — replay-attack protection per Slack's signing spec.
_MAX_REQUEST_AGE_SECONDS = 60 * 5


# ---------------------------------------------------------------------------
# Request signature verification
# ---------------------------------------------------------------------------

def _verify_slack_signature(
    signing_secret: str,
    timestamp: Optional[str],
    raw_body: bytes,
    signature: Optional[str],
) -> bool:
    """Verify a Slack request per https://api.slack.com/authentication/verifying-requests-from-slack.

    Returns True when the signing secret is empty/None (verification
    disabled — used in tests and local dev without a configured secret).
    Otherwise verifies both the HMAC signature and that the timestamp is
    within _MAX_REQUEST_AGE_SECONDS of now (replay protection).
    """
    if not signing_secret:
        return True

    if not timestamp or not signature:
        return False

    try:
        request_time = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - request_time) > _MAX_REQUEST_AGE_SECONDS:
        return False

    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8')}".encode("utf-8")
    computed = "v0=" + hmac.new(
        signing_secret.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(computed, signature)


# ---------------------------------------------------------------------------
# Default Slack client factory
# ---------------------------------------------------------------------------

def _default_slack_client_factory(token: str) -> Any:
    """Return a slack_sdk.WebClient; imported lazily so the SDK is optional
    when running tests that inject a mock."""
    try:
        from slack_sdk import WebClient  # type: ignore[import]
        return WebClient(token=token)
    except ImportError as exc:
        raise RuntimeError(
            "slack_sdk is not installed. "
            "Install it with: pip install slack_sdk"
        ) from exc


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    queue: MessageQueue,
    slack_client_factory: Optional[Callable[[str], Any]] = None,
    config_overrides: Optional[dict[str, Any]] = None,
) -> Flask:
    """Create and return the configured Flask application.

    Parameters
    ----------
    queue:
        The MessageQueue that signals are written to (and removed from on
        reaction_removed).
    slack_client_factory:
        A callable that accepts a Slack bot token string and returns an object
        with a ``conversations_history`` method matching the slack_sdk API.
        Defaults to creating a real slack_sdk.WebClient.
    config_overrides:
        Optional dict of config values to override module-level defaults.
        Recognised keys: INTAKE_EMOJI, MONITORED_CHANNELS, SLACK_SIGNING_SECRET.
    """
    # -- resolve effective config ----------------------------------------
    cfg: dict[str, Any] = {
        "INTAKE_EMOJI": _default_config.INTAKE_EMOJI,
        "MONITORED_CHANNELS": list(_default_config.MONITORED_CHANNELS),
        "SLACK_SIGNING_SECRET": _default_config.SLACK_SIGNING_SECRET,
    }
    if config_overrides:
        cfg.update(config_overrides)

    if slack_client_factory is None:
        slack_client_factory = _default_slack_client_factory

    app = Flask(__name__)

    # -----------------------------------------------------------------------
    # Route: liveness probe
    # -----------------------------------------------------------------------
    @app.get("/healthz")
    def healthz() -> tuple[Response, int]:
        return jsonify({"status": "ok"}), 200

    # -----------------------------------------------------------------------
    # Route: Slack event callbacks
    # -----------------------------------------------------------------------
    @app.post("/slack/events")
    def slack_events() -> tuple[Response, int]:
        """Handle all inbound Slack events."""
        if not _verify_slack_signature(
            signing_secret=cfg["SLACK_SIGNING_SECRET"],
            timestamp=request.headers.get("X-Slack-Request-Timestamp"),
            raw_body=request.get_data(),
            signature=request.headers.get("X-Slack-Signature"),
        ):
            logger.warning("Rejected Slack event: signature verification failed")
            return jsonify({"error": "invalid signature"}), 401

        body: dict[str, Any] = request.get_json(silent=True) or {}

        # -- URL verification challenge ------------------------------------
        if body.get("type") == "url_verification":
            return jsonify({"challenge": body.get("challenge", "")}), 200

        # -- Extract inner event ------------------------------------------
        event: dict[str, Any] = body.get("event", {})
        event_type: str = event.get("type", "")

        try:
            if event_type == "reaction_added":
                return _handle_reaction_added(event, queue, cfg, slack_client_factory)

            if event_type == "reaction_removed":
                return _handle_reaction_removed(event, queue, cfg)

            if event_type == "message":
                return _handle_message(event, queue, cfg)

        except Exception:  # pylint: disable=broad-except
            logger.exception("Unhandled exception processing Slack event")
            # Fall through → return 200 so Slack doesn't retry
            return jsonify({"ok": True}), 200

        # -- Unknown / ignored event types --------------------------------
        return jsonify({"ok": True}), 200

    return app


# ---------------------------------------------------------------------------
# Event handlers (pure functions — easier to unit-test independently)
# ---------------------------------------------------------------------------

def _handle_reaction_added(
    event: dict[str, Any],
    queue: MessageQueue,
    cfg: dict[str, Any],
    slack_client_factory: Callable[[str], Any],
) -> tuple[Response, int]:
    """Process a reaction_added event."""
    reaction_name: str = event.get("reaction", "")
    if reaction_name != cfg["INTAKE_EMOJI"]:
        return jsonify({"ok": True}), 200

    item: dict[str, Any] = event.get("item", {})
    channel_id: str = item.get("channel", "")
    message_ts: str = item.get("ts", "")
    reactor_id: str = event.get("user", "")
    reactor_name: str = event.get("user_name", reactor_id)

    # Fetch the original message so we can get its text and thread_ts.
    raw_content, thread_ts = _fetch_message_content(
        channel_id=channel_id,
        message_ts=message_ts,
        slack_client_factory=slack_client_factory,
        cfg=cfg,
    )

    signal = Signal(
        source="slack",
        type="manual_reaction",
        raw_content=raw_content,
        timestamp=_ts_to_datetime(message_ts),
        ingested_at=datetime.now(tz=timezone.utc),
        originator=Originator(id=reactor_id, name=reactor_name),
        metadata=SlackMetadata(
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=thread_ts if thread_ts else None,
            reaction=reaction_name,
            reactor_id=reactor_id,
        ),
    )

    try:
        queue.enqueue(signal)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Queue.enqueue raised an exception")
        return jsonify({"error": "queue unavailable"}), 503

    return jsonify({"ok": True}), 200


def _handle_reaction_removed(
    event: dict[str, Any],
    queue: MessageQueue,
    cfg: dict[str, Any],
) -> tuple[Response, int]:
    """Process a reaction_removed event.

    Removes the queued Signal whose metadata.message_ts AND reactor_id match
    the unreacted message/user, and that has not yet been dequeued by the
    classifier. Matching on reactor_id (in addition to message_ts) ensures
    that when multiple users react to the same message, one user removing
    their reaction only withdraws their own signal — not everyone else's.
    This makes the gate a toggle, not a latch.
    """
    reaction_name: str = event.get("reaction", "")
    if reaction_name != cfg["INTAKE_EMOJI"]:
        return jsonify({"ok": True}), 200

    item: dict[str, Any] = event.get("item", {})
    message_ts: str = item.get("ts", "")
    reactor_id: str = event.get("user", "")

    # InMemoryQueue (and any compliant queue) exposes its internal list or a
    # remove_if method.  We use duck-typing: if the queue has a _queue
    # attribute we mutate it directly; otherwise we call remove_signal if
    # present; otherwise we silently no-op (idempotent).
    _remove_signal_by_ts(queue, message_ts, reactor_id)

    return jsonify({"ok": True}), 200


def _handle_message(
    event: dict[str, Any],
    queue: MessageQueue,
    cfg: dict[str, Any],
) -> tuple[Response, int]:
    """Process a message event from a bot."""
    subtype: str = event.get("subtype", "")
    bot_id: str = event.get("bot_id", "")
    is_bot = subtype == "bot_message" or bool(bot_id)

    if not is_bot:
        return jsonify({"ok": True}), 200

    channel_id: str = event.get("channel", "")
    if channel_id not in cfg["MONITORED_CHANNELS"]:
        return jsonify({"ok": True}), 200

    message_ts: str = event.get("ts", "")
    raw_content: str = event.get("text", "")
    bot_username: str = event.get("username", event.get("bot_profile", {}).get("name", ""))

    signal = Signal(
        source="slack",
        type="bot_message",
        raw_content=raw_content,
        timestamp=_ts_to_datetime(message_ts),
        ingested_at=datetime.now(tz=timezone.utc),
        originator=Originator(id=bot_id, name=bot_username),
        metadata=SlackMetadata(
            channel_id=channel_id,
            message_ts=message_ts,
            bot_name=bot_username,
        ),
    )

    try:
        queue.enqueue(signal)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Queue.enqueue raised an exception")
        return jsonify({"error": "queue unavailable"}), 503

    return jsonify({"ok": True}), 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_message_content(
    channel_id: str,
    message_ts: str,
    slack_client_factory: Callable[[str], Any],
    cfg: dict[str, Any],
) -> tuple[str, str | None]:
    """Fetch the message text (and thread replies if it is a thread parent).

    Returns (raw_content, thread_ts).
    thread_ts is None when the message has no thread context.
    """
    token: str = cfg.get("SLACK_BOT_TOKEN", "")
    client = slack_client_factory(token)

    try:
        response = client.conversations_history(
            channel=channel_id,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )
        messages: list[dict[str, Any]] = response.get("messages", [])
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to fetch message from Slack API")
        return "", None

    if not messages:
        return "", None

    message: dict[str, Any] = messages[0]
    text: str = message.get("text", "")
    thread_ts: str | None = message.get("thread_ts") or None

    # If this message is a thread parent (message_ts == thread_ts), fetch
    # replies and append them to raw_content.
    if thread_ts and thread_ts == message_ts:
        try:
            replies_response = client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
            )
            reply_messages: list[dict[str, Any]] = replies_response.get("messages", [])
            # Skip the first element — it is the parent message itself.
            replies_text = "\n".join(
                m.get("text", "") for m in reply_messages[1:]
            )
            if replies_text:
                text = text + "\n" + replies_text
        except Exception:  # pylint: disable=broad-except
            logger.exception("Failed to fetch thread replies from Slack API")

    return text, thread_ts


def _remove_signal_by_ts(queue: MessageQueue, message_ts: str, reactor_id: str) -> None:
    """Remove the Signal whose (message_ts, reactor_id) matches, if still queued.

    Matching on reactor_id in addition to message_ts prevents a race where
    two users react to the same message and one withdrawing their reaction
    incorrectly removes both queued signals — only the withdrawing user's
    own signal should be dropped.

    Works with InMemoryQueue by directly mutating _queue. For other queue
    implementations, calls remove_signal(message_ts, reactor_id) if that
    method exists. Falls back to no-op so removal is always idempotent.
    """
    if hasattr(queue, "_queue"):
        # InMemoryQueue path: mutate in-place
        queue._queue = [  # type: ignore[attr-defined]
            s for s in queue._queue  # type: ignore[attr-defined]
            if not (
                isinstance(s.metadata, SlackMetadata)
                and s.metadata.message_ts == message_ts
                and s.metadata.reactor_id == reactor_id
            )
        ]
    elif hasattr(queue, "remove_signal"):
        queue.remove_signal(message_ts, reactor_id)  # type: ignore[attr-defined]
    # else: silently no-op — idempotent


def _ts_to_datetime(ts: str) -> datetime:
    """Convert a Slack timestamp string (e.g. '1700000000.123456') to datetime."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(tz=timezone.utc)

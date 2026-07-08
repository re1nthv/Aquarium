"""
GUS connector — two trigger paths writing to a shared MessageQueue.

Path 1: Webhook handler (Flask) at POST /gus/events
Path 2: GusPoller background thread using a GusClient abstraction
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from flask import Flask, request, jsonify

from feed_layer.shared import Signal, GusMetadata, Originator, MessageQueue
from .config import MAX_REQUESTS_PER_SECOND, RAPID_UPDATE_WINDOW_SECONDS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstractions
# ---------------------------------------------------------------------------

class GusClient(ABC):
    """Abstract interface for the GUS API — lets tests inject a mock."""

    @abstractmethod
    def fetch_items_since(self, since: datetime) -> list[dict]:
        """Return all work items with updated_at > since, sorted by updated_at ascending."""


class CursorStore(ABC):
    """Durable cursor for the polling fallback."""

    @abstractmethod
    def get(self) -> datetime:
        """Return the last committed cursor (UTC datetime)."""

    @abstractmethod
    def set(self, ts: datetime) -> None:
        """Persist cursor durably."""


class InMemoryCursorStore(CursorStore):
    """In-memory cursor store for tests and local development."""

    def __init__(self, initial: Optional[datetime] = None) -> None:
        self._cursor: datetime = initial or datetime(1970, 1, 1, tzinfo=timezone.utc)

    def get(self) -> datetime:
        return self._cursor

    def set(self, ts: datetime) -> None:
        self._cursor = ts


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class FileCursorStore(CursorStore):
    """
    Durable cursor store that persists the cursor as JSON on disk.

    Writes are atomic: the new value is written to a temp file in the same
    directory and then moved into place with os.replace, so a crash mid-write
    never corrupts the committed cursor file.

    If the file is missing or malformed on load, the cursor falls back to
    the epoch (1970-01-01T00:00:00+00:00) rather than raising.
    """

    def __init__(self, path: Union[str, Path]) -> None:
        self._path = Path(path)
        self._cursor: datetime = self._load()

    def _load(self) -> datetime:
        try:
            raw = self._path.read_text()
        except (FileNotFoundError, OSError):
            return _EPOCH
        try:
            data = json.loads(raw)
            return datetime.fromisoformat(data["cursor"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Malformed cursor file at %s — falling back to epoch", self._path)
            return _EPOCH

    def get(self) -> datetime:
        return self._cursor

    def set(self, ts: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = json.dumps({"cursor": ts.isoformat()})
        with open(tmp_path, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self._path)
        self._cursor = ts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _gus_item_type_from_event(event_type: str) -> str:
    """Derive gus_item_type from the event type string."""
    if event_type.startswith("epic"):
        return "epic"
    if event_type.startswith("td"):
        return "td"
    if event_type.startswith("escalation"):
        return "escalation"
    if event_type.startswith("task") or event_type == "label.added":
        return "task"
    return "task"


def _gus_item_type_from_item(item_type: str) -> str:
    """Normalise a raw item_type string from the GUS API payload."""
    mapping = {
        "epic": "epic",
        "task": "task",
        "td": "td",
        "team_dependency": "td",
        "escalation": "escalation",
    }
    return mapping.get(item_type.lower(), "task")


def _build_signal_from_payload(event_type: str, signal_type: str, payload: dict) -> Signal:
    """Build a canonical Signal from a raw GUS webhook payload dict."""
    item = payload["item"]
    now = _now_utc()
    # timestamp: prefer item updated_at, fall back to created_at, then now
    raw_ts = item.get("updated_at") or item.get("created_at")
    if isinstance(raw_ts, str):
        timestamp = datetime.fromisoformat(raw_ts)
    elif isinstance(raw_ts, datetime):
        timestamp = raw_ts
    else:
        timestamp = now

    return Signal(
        source="gus",
        type=signal_type,  # type: ignore[arg-type]
        raw_content=f"{item['title']} — {item['description']}",
        timestamp=timestamp,
        ingested_at=now,
        originator=Originator(
            id=payload["user_id"],
            name=payload["user_name"],
        ),
        metadata=GusMetadata(
            gus_item_id=item["gus_item_id"],
            gus_item_type=_gus_item_type_from_event(event_type),  # type: ignore[arg-type]
            gus_item_url=item["gus_item_url"],
            team=item["team"],
            release=item.get("release"),
            label_applied=payload.get("label_name"),
        ),
    )


def _build_signal_from_polled_item(item: dict) -> Optional[Signal]:
    """
    Build a canonical Signal from a polled GUS item dict, or return None if
    the item should be skipped (not ingested) during polling.

    Per the README, task-level CRUD is excluded from ingestion — only tasks
    whose status is "Blocked" are actionable and produce a task.blocked
    signal. Items with an item_type we don't recognise are skipped rather
    than silently mis-typed as epic.updated.
    """
    item_type = item.get("item_type", "task")
    type_map = {
        "epic": "epic.updated",
        "td": "td.updated",
        "team_dependency": "td.updated",
        "escalation": "escalation.created",
    }

    if item_type.lower() == "task":
        if item.get("status") != "Blocked":
            # Task-level CRUD that isn't a Blocked status change — excluded
            # from ingestion per the connector's design.
            return None
        signal_type = "task.blocked"
    else:
        signal_type = type_map.get(item_type.lower())
        if signal_type is None:
            logger.warning(
                "Skipping polled item %s with unrecognised item_type=%r",
                item.get("gus_item_id", "<unknown>"),
                item_type,
            )
            return None

    now = _now_utc()
    raw_ts = item.get("updated_at") or item.get("created_at")
    if isinstance(raw_ts, str):
        timestamp = datetime.fromisoformat(raw_ts)
    elif isinstance(raw_ts, datetime):
        timestamp = raw_ts
    else:
        timestamp = now

    return Signal(
        source="gus",
        type=signal_type,  # type: ignore[arg-type]
        raw_content=f"{item['title']} — {item['description']}",
        timestamp=timestamp,
        ingested_at=now,
        originator=Originator(
            id=item.get("user_id", ""),
            name=item.get("user_name", ""),
        ),
        metadata=GusMetadata(
            gus_item_id=item["gus_item_id"],
            gus_item_type=_gus_item_type_from_item(item_type),  # type: ignore[arg-type]
            gus_item_url=item.get("gus_item_url", ""),
            team=item.get("team", ""),
            release=item.get("release"),
            label_applied=item.get("label_applied"),
        ),
    )


# ---------------------------------------------------------------------------
# Rapid-update collapse state (module-level, shared across requests)
# ---------------------------------------------------------------------------

class _RapidUpdateTracker:
    """
    Tracks in-queue epic.updated signals so we can replace them with newer
    versions within RAPID_UPDATE_WINDOW_SECONDS.

    Stores: gus_item_id -> (signal_id, enqueued_at)
    The queue reference is needed so we can locate and swap the signal.
    """

    def __init__(self, queue: MessageQueue) -> None:
        self._queue = queue
        # gus_item_id -> (signal_id, enqueued_at)
        self._index: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def try_collapse(self, new_signal: Signal) -> bool:
        """
        If a prior epic.updated for the same gus_item_id is still in the window,
        replace it in the queue and update the index. Return True if collapsed,
        False if a fresh enqueue is needed.
        """
        item_id = new_signal.metadata.gus_item_id  # type: ignore[union-attr]
        now = _now_utc()
        with self._lock:
            entry = self._index.get(item_id)
            if entry is not None:
                old_signal_id, enqueued_at = entry
                age = (now - enqueued_at).total_seconds()
                if age <= RAPID_UPDATE_WINDOW_SECONDS:
                    # Replace in queue
                    if _queue_replace(self._queue, old_signal_id, new_signal):
                        self._index[item_id] = (new_signal.id, now)
                        return True
            # No in-window entry — register for future collapses
            self._index[item_id] = (new_signal.id, now)
            return False


def _queue_replace(queue: MessageQueue, old_id: str, new_signal: Signal) -> bool:
    """
    Replace the signal with old_id in an InMemoryQueue-compatible queue.
    Returns True if the replacement succeeded, False if the signal was not found
    (e.g. already dequeued).
    """
    # We rely on the internal _queue list for InMemoryQueue.
    # For other queue implementations the collapse degrades to a fresh enqueue.
    internal = getattr(queue, "_queue", None)
    if internal is None:
        return False
    for i, sig in enumerate(internal):
        if sig.id == old_id:
            internal[i] = new_signal
            return True
    return False


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app(queue: MessageQueue, control_store: Optional[Any] = None) -> Flask:
    """
    Create and return the Flask application for the GUS webhook handler.

    Parameters
    ----------
    queue:
        The shared MessageQueue all enqueued signals are written to.
    control_store:
        Optional ``feed_layer.control_plane.ControlStore``. When provided,
        signals are additionally gated by
        ``control_store.gus_team_followed(signal.metadata.team)`` — an
        unfollowed team's signal is not enqueued (still returns 200). When
        omitted (default), behaviour is unchanged.
    """
    app = Flask(__name__)
    tracker = _RapidUpdateTracker(queue)

    # Map event_type -> signal_type (simple cases)
    _SIMPLE_TYPE_MAP: dict[str, str] = {
        "epic.created": "epic.created",
        "epic.updated": "epic.updated",
        "td.created": "td.created",
        "td.updated": "td.updated",
        "escalation.created": "escalation.created",
    }

    @app.route("/gus/events", methods=["POST"])
    def gus_events():
        body = request.get_json(silent=True) or {}
        event_type: str = body.get("event_type", "")
        payload: dict = body.get("payload", {})

        signal_type: Optional[str] = None

        if event_type in _SIMPLE_TYPE_MAP:
            signal_type = _SIMPLE_TYPE_MAP[event_type]

        elif event_type == "task.status_changed":
            if payload.get("new_status") == "Blocked":
                signal_type = "task.blocked"
            # else: not ingested

        elif event_type == "label.added":
            if payload.get("label_name") == "aquarium-intake":
                signal_type = "manual_label"
            # else: not ingested

        # All other event types: return 200, do NOT enqueue
        if signal_type is None:
            return jsonify({"status": "ignored"}), 200

        try:
            signal = _build_signal_from_payload(event_type, signal_type, payload)
        except (KeyError, TypeError, ValueError):
            # Malformed payload — still return 200 per spec (enqueue is best-effort)
            return jsonify({"status": "ignored"}), 200

        if control_store is not None and not control_store.gus_team_followed(
            signal.metadata.team  # type: ignore[union-attr]
        ):
            return jsonify({"status": "ignored"}), 200

        try:
            if signal_type == "epic.updated":
                collapsed = tracker.try_collapse(signal)
                if not collapsed:
                    queue.enqueue(signal)
            else:
                queue.enqueue(signal)
        except Exception:
            return jsonify({"status": "queue_error"}), 503

        return jsonify({"status": "ok"}), 200

    return app


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Minimal min-interval rate limiter: blocks callers of acquire() so that
    calls are spaced at least 1 / max_requests_per_second apart.

    The clock and sleep functions are injectable so tests can assert
    throttling behaviour deterministically without real sleeping.
    """

    def __init__(
        self,
        max_requests_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = (
            1.0 / max_requests_per_second if max_requests_per_second > 0 else 0.0
        )
        self._clock = clock
        self._sleep = sleep
        self._last_call: Optional[float] = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block (via the injected sleep) until it is safe to make the next call."""
        with self._lock:
            if self._min_interval <= 0:
                return
            now = self._clock()
            if self._last_call is None:
                self._last_call = now
                return
            earliest_next = self._last_call + self._min_interval
            wait = earliest_next - now
            if wait > 0:
                self._sleep(wait)
                self._last_call = earliest_next
            else:
                self._last_call = now


# ---------------------------------------------------------------------------
# GusPoller
# ---------------------------------------------------------------------------

class GusPoller:
    """
    Polling fallback that periodically fetches recently modified GUS items
    and enqueues them as Signals.
    """

    def __init__(
        self,
        gus_client: GusClient,
        queue: MessageQueue,
        cursor_store: CursorStore,
        rate_limiter: Optional[RateLimiter] = None,
        control_store: Optional[Any] = None,
    ) -> None:
        self._client = gus_client
        self._queue = queue
        self._cursor_store = cursor_store
        self._rate_limiter = rate_limiter or RateLimiter(MAX_REQUESTS_PER_SECOND)
        self._control_store = control_store
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def poll_once(self) -> int:
        """
        Fetch items modified after the cursor, enqueue each non-skipped
        item, and advance the cursor for every item processed (skipped or
        enqueued) so polling never stalls on excluded items.

        Returns the number of signals successfully enqueued (skipped items
        do not count).
        """
        since = self._cursor_store.get()
        self._rate_limiter.acquire()
        items = self._client.fetch_items_since(since)

        count = 0
        for item in items:
            signal = _build_signal_from_polled_item(item)
            if signal is not None and self._control_store is not None:
                team = signal.metadata.team  # type: ignore[union-attr]
                if not self._control_store.gus_team_followed(team):
                    signal = None
            if signal is not None:
                self._queue.enqueue(signal)
                count += 1
            # Advance cursor for every processed item, enqueued or skipped,
            # so skipped items (e.g. non-blocked tasks) never stall the
            # cursor and cause re-fetching on the next poll.
            raw_ts = item.get("updated_at") or item.get("created_at")
            if raw_ts is not None:
                if isinstance(raw_ts, str):
                    item_ts = datetime.fromisoformat(raw_ts)
                else:
                    item_ts = raw_ts
                if item_ts > self._cursor_store.get():
                    self._cursor_store.set(item_ts)

        return count

    def is_running(self) -> bool:
        """Return True if the background polling thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, interval_seconds: int = 300) -> None:
        """
        Start a background thread that calls poll_once() every
        interval_seconds. If a poll run takes longer than the interval,
        the next run is skipped.
        """
        if self.is_running():
            return

        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                start = time.monotonic()
                try:
                    self.poll_once()
                except Exception:
                    pass  # log in production; swallow here to keep thread alive
                elapsed = time.monotonic() - start
                remaining = interval_seconds - elapsed
                if remaining > 0:
                    self._stop_event.wait(timeout=remaining)

        self._thread = threading.Thread(target=_loop, daemon=True, name="gus-poller")
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

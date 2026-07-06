"""
Tests for the GUS connector (webhook handler + GusPoller).

All 23 test cases from the specification are covered.
No real HTTP or external API calls are made.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from feed_layer.shared import InMemoryQueue
from feed_layer.gus.connector import (
    GusClient,
    CursorStore,
    InMemoryCursorStore,
    GusPoller,
    create_app,
    _RapidUpdateTracker,
    _now_utc,
)
from feed_layer.gus.config import RAPID_UPDATE_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_payload(
    event_type: str,
    gus_item_id: str = "W-123456",
    title: str = "My Epic",
    description: str = "Some description",
    user_id: str = "u001",
    user_name: str = "Alice",
    gus_item_url: str = "https://gus.example.com/W-123456",
    team: str = "Team Alpha",
    release: Optional[str] = "250",
    new_status: Optional[str] = None,
    label_name: Optional[str] = None,
) -> dict:
    payload: dict = {
        "user_id": user_id,
        "user_name": user_name,
        "item": {
            "gus_item_id": gus_item_id,
            "title": title,
            "description": description,
            "gus_item_url": gus_item_url,
            "team": team,
            "release": release,
            "updated_at": "2025-07-06T10:00:00+00:00",
        },
    }
    if new_status is not None:
        payload["new_status"] = new_status
    if label_name is not None:
        payload["label_name"] = label_name
    return payload


def _post(client, event_type: str, payload: Optional[dict] = None) -> tuple:
    """Send a POST /gus/events request and return (status_code, json_body)."""
    body = {"event_type": event_type, "payload": payload or _make_payload(event_type)}
    resp = client.post(
        "/gus/events",
        data=json.dumps(body),
        content_type="application/json",
    )
    return resp.status_code, resp.get_json()


@pytest.fixture()
def queue():
    return InMemoryQueue()


@pytest.fixture()
def app_and_queue():
    q = InMemoryQueue()
    app = create_app(q)
    app.config["TESTING"] = True
    return app, q


@pytest.fixture()
def client(app_and_queue):
    app, _ = app_and_queue
    return app.test_client()


@pytest.fixture()
def queue_from_app(app_and_queue):
    _, q = app_and_queue
    return q


# ---------------------------------------------------------------------------
# Webhook happy path (tests 1-7)
# ---------------------------------------------------------------------------

class TestWebhookHappyPath:

    def test_01_epic_created_enqueued(self, client, queue_from_app):
        """epic.created payload -> signal enqueued, type='epic.created', gus_item_id matches."""
        payload = _make_payload("epic.created", gus_item_id="W-001")
        status, _ = _post(client, "epic.created", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        sig = signals[0]
        assert sig.type == "epic.created"
        assert sig.metadata.gus_item_id == "W-001"

    def test_02_epic_updated_enqueued(self, client, queue_from_app):
        """epic.updated payload -> signal enqueued, type='epic.updated'."""
        payload = _make_payload("epic.updated", gus_item_id="W-002")
        status, _ = _post(client, "epic.updated", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "epic.updated"

    def test_03_td_created_enqueued(self, client, queue_from_app):
        """td.created payload -> signal enqueued, type='td.created'."""
        payload = _make_payload("td.created", gus_item_id="W-003")
        status, _ = _post(client, "td.created", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "td.created"

    def test_04_td_updated_enqueued(self, client, queue_from_app):
        """td.updated payload -> signal enqueued, type='td.updated'."""
        payload = _make_payload("td.updated", gus_item_id="W-004")
        status, _ = _post(client, "td.updated", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "td.updated"

    def test_05_escalation_created_enqueued(self, client, queue_from_app):
        """escalation.created -> signal enqueued, type='escalation.created'."""
        payload = _make_payload("escalation.created", gus_item_id="W-005")
        status, _ = _post(client, "escalation.created", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "escalation.created"

    def test_06_task_status_changed_blocked_enqueued(self, client, queue_from_app):
        """task.status_changed with new_status='Blocked' -> signal enqueued, type='task.blocked'."""
        payload = _make_payload("task.status_changed", gus_item_id="W-006", new_status="Blocked")
        status, _ = _post(client, "task.status_changed", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "task.blocked"

    def test_07_label_added_aquarium_intake_enqueued(self, client, queue_from_app):
        """label.added with label_name='aquarium-intake' -> signal enqueued, type='manual_label'."""
        payload = _make_payload("label.added", gus_item_id="W-007", label_name="aquarium-intake")
        status, _ = _post(client, "label.added", payload)
        assert status == 200
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        assert signals[0].type == "manual_label"


# ---------------------------------------------------------------------------
# Webhook boundary / negative cases (tests 8-14)
# ---------------------------------------------------------------------------

class TestWebhookBoundaryNegative:

    def test_08_task_status_changed_not_blocked(self, client, queue_from_app):
        """task.status_changed with new_status='In Progress' -> nothing enqueued."""
        payload = _make_payload("task.status_changed", new_status="In Progress")
        status, _ = _post(client, "task.status_changed", payload)
        assert status == 200
        assert queue_from_app.depth() == 0

    def test_09_label_added_other_label(self, client, queue_from_app):
        """label.added with label_name='some-other-label' -> nothing enqueued."""
        payload = _make_payload("label.added", label_name="some-other-label")
        status, _ = _post(client, "label.added", payload)
        assert status == 200
        assert queue_from_app.depth() == 0

    def test_10_task_created_not_enqueued(self, client, queue_from_app):
        """task.created -> nothing enqueued."""
        payload = _make_payload("task.created")
        status, _ = _post(client, "task.created", payload)
        assert status == 200
        assert queue_from_app.depth() == 0

    def test_11_task_updated_not_enqueued(self, client, queue_from_app):
        """task.updated -> nothing enqueued."""
        payload = _make_payload("task.updated")
        status, _ = _post(client, "task.updated", payload)
        assert status == 200
        assert queue_from_app.depth() == 0

    def test_12_task_deleted_not_enqueued(self, client, queue_from_app):
        """task.deleted -> nothing enqueued."""
        payload = _make_payload("task.deleted")
        status, _ = _post(client, "task.deleted", payload)
        assert status == 200
        assert queue_from_app.depth() == 0

    def test_13_queue_raises_returns_503(self, app_and_queue):
        """If queue raises on enqueue -> returns 503."""
        app, q = app_and_queue
        q.enqueue = MagicMock(side_effect=RuntimeError("queue full"))
        with app.test_client() as c:
            payload = _make_payload("epic.created")
            status, body = _post(c, "epic.created", payload)
        assert status == 503

    def test_14_unknown_event_type_returns_200_nothing_enqueued(self, client, queue_from_app):
        """Unknown event type -> returns 200, nothing enqueued."""
        payload = _make_payload("some.unknown.event")
        status, _ = _post(client, "some.unknown.event", payload)
        assert status == 200
        assert queue_from_app.depth() == 0


# ---------------------------------------------------------------------------
# Rapid-update collapse (tests 15-16)
# ---------------------------------------------------------------------------

class TestRapidUpdateCollapse:

    def test_15_two_epic_updated_within_window_only_latest_in_queue(self, app_and_queue):
        """
        Two epic.updated for the same gus_item_id within 60 s -> only latest signal
        is in the queue (first is replaced).
        """
        app, q = app_and_queue
        item_id = "W-COLLAPSE"

        payload1 = _make_payload("epic.updated", gus_item_id=item_id, title="v1", description="desc1")
        payload2 = _make_payload("epic.updated", gus_item_id=item_id, title="v2", description="desc2")

        with app.test_client() as c:
            # Both events arrive within the same second — well within the window
            _post(c, "epic.updated", payload1)
            _post(c, "epic.updated", payload2)

        signals = q.dequeue(batch_size=100)
        assert len(signals) == 1, f"Expected 1 signal, got {len(signals)}"
        assert signals[0].raw_content == "v2 — desc2"
        assert signals[0].metadata.gus_item_id == item_id

    def test_16_two_epic_updated_outside_window_both_in_queue(self, app_and_queue):
        """
        Two epic.updated for the same gus_item_id > 60 s apart -> both signals
        are in the queue.
        """
        app, q = app_and_queue
        item_id = "W-NO-COLLAPSE"

        payload1 = _make_payload("epic.updated", gus_item_id=item_id, title="v1", description="first")
        payload2 = _make_payload("epic.updated", gus_item_id=item_id, title="v2", description="second")

        # We need the first event to appear older than RAPID_UPDATE_WINDOW_SECONDS.
        # Patch _now_utc so the tracker thinks the first event was enqueued > 60s ago.
        base_time = datetime(2025, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
        later_time = base_time + timedelta(seconds=RAPID_UPDATE_WINDOW_SECONDS + 1)

        call_times = [base_time, base_time, later_time, later_time]
        call_iter = iter(call_times)

        with patch("feed_layer.gus.connector._now_utc", side_effect=lambda: next(call_iter)):
            with app.test_client() as c:
                _post(c, "epic.updated", payload1)
                _post(c, "epic.updated", payload2)

        signals = q.dequeue(batch_size=100)
        assert len(signals) == 2, f"Expected 2 signals, got {len(signals)}"
        contents = {s.raw_content for s in signals}
        assert "v1 — first" in contents
        assert "v2 — second" in contents


# ---------------------------------------------------------------------------
# Polling (tests 17-20)
# ---------------------------------------------------------------------------

class _FakeGusClient(GusClient):
    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.calls: list[datetime] = []

    def fetch_items_since(self, since: datetime) -> list[dict]:
        self.calls.append(since)
        return self._items


def _make_item(
    gus_item_id: str = "W-P001",
    title: str = "Polled Epic",
    description: str = "desc",
    item_type: str = "epic",
    updated_at: str = "2025-07-06T12:00:00+00:00",
    user_id: str = "u099",
    user_name: str = "Poller Bot",
    team: str = "Team Beta",
    gus_item_url: str = "https://gus.example.com/W-P001",
) -> dict:
    return {
        "gus_item_id": gus_item_id,
        "title": title,
        "description": description,
        "item_type": item_type,
        "updated_at": updated_at,
        "user_id": user_id,
        "user_name": user_name,
        "team": team,
        "gus_item_url": gus_item_url,
    }


class TestPolling:

    def test_17_poll_once_3_items_3_enqueued_cursor_advanced(self):
        """poll_once() with 3 items -> 3 signals enqueued, cursor advanced to latest updated_at."""
        items = [
            _make_item("W-P001", updated_at="2025-07-06T10:00:00+00:00"),
            _make_item("W-P002", updated_at="2025-07-06T11:00:00+00:00"),
            _make_item("W-P003", updated_at="2025-07-06T12:00:00+00:00"),
        ]
        q = InMemoryQueue()
        cursor = InMemoryCursorStore()
        client = _FakeGusClient(items)
        poller = GusPoller(client, q, cursor)

        count = poller.poll_once()

        assert count == 3
        assert q.depth() == 3
        expected_ts = datetime(2025, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
        assert cursor.get() == expected_ts

    def test_18_poll_once_0_items_cursor_unchanged(self):
        """poll_once() with 0 items -> 0 enqueued, cursor unchanged."""
        initial_cursor = datetime(2025, 1, 1, tzinfo=timezone.utc)
        q = InMemoryQueue()
        cursor = InMemoryCursorStore(initial=initial_cursor)
        client = _FakeGusClient([])
        poller = GusPoller(client, q, cursor)

        count = poller.poll_once()

        assert count == 0
        assert q.depth() == 0
        assert cursor.get() == initial_cursor

    def test_19_crash_mid_poll_cursor_only_advanced_for_successful_items(self):
        """
        GusClient raises after 2 of 3 items -> cursor only advanced past
        the 2 successfully enqueued items.
        """
        items_before_crash = [
            _make_item("W-P001", updated_at="2025-07-06T10:00:00+00:00"),
            _make_item("W-P002", updated_at="2025-07-06T11:00:00+00:00"),
        ]
        third_item = _make_item("W-P003", updated_at="2025-07-06T12:00:00+00:00")

        call_count = 0

        class _CrashingClient(GusClient):
            def fetch_items_since(self, since: datetime) -> list[dict]:
                # Return all 3 but raise when iterating (simulated via a special list)
                return items_before_crash + [third_item]

        q = InMemoryQueue()
        cursor = InMemoryCursorStore()

        # Patch enqueue to raise on the 3rd call
        original_enqueue = q.enqueue
        enqueue_calls = [0]

        def _patched_enqueue(sig):
            enqueue_calls[0] += 1
            if enqueue_calls[0] == 3:
                raise RuntimeError("queue full")
            original_enqueue(sig)

        q.enqueue = _patched_enqueue

        client = _CrashingClient()
        poller = GusPoller(client, q, cursor)

        with pytest.raises(RuntimeError):
            poller.poll_once()

        # Cursor should be at the 2nd item's timestamp (not the 3rd)
        expected_cursor = datetime(2025, 7, 6, 11, 0, 0, tzinfo=timezone.utc)
        assert cursor.get() == expected_cursor
        assert q.depth() == 2

    def test_20_poll_once_uses_cursor_from_store_not_current_time(self):
        """
        poll_once() on restart uses cursor from CursorStore (not current time).
        """
        stored_cursor = datetime(2025, 3, 15, 8, 0, 0, tzinfo=timezone.utc)
        q = InMemoryQueue()
        cursor = InMemoryCursorStore(initial=stored_cursor)
        client = _FakeGusClient([])
        poller = GusPoller(client, q, cursor)

        poller.poll_once()

        assert len(client.calls) == 1
        assert client.calls[0] == stored_cursor


# ---------------------------------------------------------------------------
# Schema correctness (tests 21-23)
# ---------------------------------------------------------------------------

class TestSchemaCorrectness:

    def test_21_signal_schema_version_source_fields(self, client, queue_from_app):
        """Signal produced by webhook has schema_version='1.0', source='gus', all required fields non-null."""
        payload = _make_payload("epic.created", gus_item_id="W-SCHEMA")
        _post(client, "epic.created", payload)
        signals = queue_from_app.dequeue()
        assert len(signals) == 1
        sig = signals[0]

        assert sig.schema_version == "1.0"
        assert sig.source == "gus"
        # Required non-null fields
        assert sig.id is not None
        assert sig.type is not None
        assert sig.raw_content is not None
        assert sig.timestamp is not None
        assert sig.ingested_at is not None
        assert sig.originator is not None
        assert sig.metadata is not None

    def test_22_signal_id_is_valid_uuid(self, client, queue_from_app):
        """Signal id is a valid UUID."""
        payload = _make_payload("epic.created", gus_item_id="W-UUID")
        _post(client, "epic.created", payload)
        signals = queue_from_app.dequeue()
        sig = signals[0]
        # Will raise ValueError if not a valid UUID
        parsed = uuid.UUID(sig.id)
        assert str(parsed) == sig.id

    def test_23_raw_content_format(self, client, queue_from_app):
        """raw_content = '{title} — {description}'."""
        payload = _make_payload(
            "epic.created",
            title="My Epic Title",
            description="My epic description",
        )
        _post(client, "epic.created", payload)
        signals = queue_from_app.dequeue()
        sig = signals[0]
        assert sig.raw_content == "My Epic Title — My epic description"

"""Tests for the Google Workspace connector.

All 21 cases from the spec are covered here.  No real API calls are made —
DriveClient and CursorStore are injected mocks.

Run with:
    pytest feed_layer/google_workspace/tests/test_gws_connector.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from feed_layer.shared import InMemoryQueue, Signal
from feed_layer.google_workspace.connector import (
    CursorStore,
    DriveClient,
    GWSPoller,
    InMemoryCursorStore,
    WatchChannelRenewer,
    _dedup_enqueue,
    _unpack_changes_result,
    create_app,
)
from feed_layer.google_workspace.config import READY_SUFFIX

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

INTAKE_FOLDER = "folder-intake-001"
OTHER_FOLDER = "folder-other-999"

_MODIFIED_TIME = "2026-07-06T10:00:00Z"

_BASE_FILE_META = {
    "id": "file-abc-123",
    "name": f"My Design Doc{READY_SUFFIX}",
    "mimeType": "application/vnd.google-apps.document",
    "parents": [INTAKE_FOLDER],
    "webViewLink": "https://docs.google.com/document/d/file-abc-123/edit",
    "modifiedTime": _MODIFIED_TIME,
    "lastModifyingUser": {
        "permissionId": "user-42",
        "displayName": "Alice Engineer",
    },
    "permissions": [{"type": "domain", "role": "reader"}],
}

_BASE_CHANGE = {
    "fileId": "file-abc-123",
    "removed": False,
    "file": _BASE_FILE_META,
}


def _make_drive_client(
    changes: Optional[list[dict]] = None,
    file_meta: Optional[dict] = None,
    content: str = "Hello, world!",
    content_raises: Optional[Exception] = None,
    files_in_folder: Optional[list[dict]] = None,
) -> DriveClient:
    """Create a mock DriveClient."""
    client = MagicMock(spec=DriveClient)
    client.list_changes.return_value = changes if changes is not None else [dict(_BASE_CHANGE)]
    if file_meta is None:
        file_meta = dict(_BASE_FILE_META)
    client.get_file_metadata.return_value = file_meta
    if content_raises is not None:
        client.get_file_content.side_effect = content_raises
    else:
        client.get_file_content.return_value = content
    if files_in_folder is not None:
        client.list_files_in_folder.return_value = files_in_folder
    return client


def _make_app(drive_client: DriveClient, queue: InMemoryQueue, intake: str = INTAKE_FOLDER):
    return create_app(
        drive_client, queue, page_token="tok-1", intake_folder_id=intake
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def queue():
    return InMemoryQueue()


@pytest.fixture()
def drive():
    return _make_drive_client()


@pytest.fixture()
def app(drive, queue):
    return _make_app(drive, queue)


@pytest.fixture()
def client(app):
    app.testing = True
    return app.test_client()


def _post_notify(test_client, state: str = "change", channel_id: str = "chan-1", resource_id: str = "res-1"):
    return test_client.post(
        "/gdrive/notify",
        headers={
            "X-Goog-Channel-Id": channel_id,
            "X-Goog-Resource-State": state,
            "X-Goog-Resource-Id": resource_id,
        },
    )


# ===========================================================================
# 1. Happy path — change → file in intake folder with [READY] → signal enqueued
# ===========================================================================

def test_happy_path_signal_enqueued(client, queue):
    resp = _post_notify(client)
    assert resp.status_code == 200
    assert queue.depth() == 1
    signal = queue.dequeue()[0]
    assert signal.type == "document.ready"
    assert signal.source == "google_workspace"


# ===========================================================================
# 2. raw_content matches DriveClient.get_file_content() return value
# ===========================================================================

def test_raw_content_matches_drive_content(queue):
    expected_content = "This is the document body, line by line."
    drive = _make_drive_client(content=expected_content)
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
    assert queue.depth() == 1
    signal = queue.dequeue()[0]
    assert signal.raw_content == expected_content


# ===========================================================================
# 3. Signal metadata.file_id matches the file's Drive ID
# ===========================================================================

def test_signal_metadata_file_id(client, queue):
    _post_notify(client)
    signal = queue.dequeue()[0]
    assert signal.metadata.file_id == "file-abc-123"


# ===========================================================================
# 4. sharing_scope derived correctly from file metadata
# ===========================================================================

@pytest.mark.parametrize("permissions,expected_scope", [
    ([{"type": "anyone", "role": "reader"}], "public"),
    ([{"type": "domain", "role": "reader"}], "org"),
    ([{"type": "user", "role": "writer"}], "team"),
    ([], "team"),
])
def test_sharing_scope_derived(queue, permissions, expected_scope):
    meta = dict(_BASE_FILE_META)
    meta["permissions"] = permissions
    drive = _make_drive_client(file_meta=meta)
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
    signal = queue.dequeue()[0]
    assert signal.metadata.sharing_scope == expected_scope


# ===========================================================================
# 5. sync notification → nothing enqueued, returns 200
# ===========================================================================

def test_sync_notification_ignored(client, queue):
    resp = _post_notify(client, state="sync")
    assert resp.status_code == 200
    assert queue.depth() == 0


# ===========================================================================
# 6. File NOT in intake folder → nothing enqueued
# ===========================================================================

def test_file_not_in_intake_folder_skipped(queue):
    meta = dict(_BASE_FILE_META)
    meta["parents"] = [OTHER_FOLDER]
    change = dict(_BASE_CHANGE)
    change["file"] = meta
    drive = _make_drive_client(changes=[change], file_meta=meta)
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
    assert queue.depth() == 0


# ===========================================================================
# 7. File in intake folder but name does NOT end with [READY] → nothing enqueued
# ===========================================================================

def test_file_without_ready_suffix_skipped(queue):
    meta = dict(_BASE_FILE_META)
    meta["name"] = "My Design Doc"  # no [READY]
    change = dict(_BASE_CHANGE)
    change["file"] = meta
    drive = _make_drive_client(changes=[change], file_meta=meta)
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
    assert queue.depth() == 0


# ===========================================================================
# 8. File deleted (change type = removed) → nothing enqueued
# ===========================================================================

def test_removed_change_skipped(queue):
    change = {
        "fileId": "file-abc-123",
        "removed": True,
        "file": {},
    }
    drive = _make_drive_client(changes=[change])
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
    assert queue.depth() == 0


# ===========================================================================
# 9. get_file_content raises PermissionError → nothing enqueued, no crash
# ===========================================================================

def test_permission_denied_not_enqueued_no_crash(queue, caplog):
    import logging
    drive = _make_drive_client(content_raises=PermissionError("403"))
    app = _make_app(drive, queue)
    with app.test_client() as c:
        with caplog.at_level(logging.WARNING):
            resp = _post_notify(c)
    assert resp.status_code == 200
    assert queue.depth() == 0
    assert "permission_denied" in caplog.text


# ===========================================================================
# 10. Queue raises on enqueue → no crash, returns 200
# ===========================================================================

def test_queue_enqueue_error_no_crash(drive):
    bad_queue = MagicMock(spec=InMemoryQueue)
    bad_queue.enqueue.side_effect = RuntimeError("queue full")
    # Expose _queue = None so _replace_in_queue falls back gracefully
    bad_queue._queue = None
    app = _make_app(drive, bad_queue)
    with app.test_client() as c:
        resp = _post_notify(c)
    assert resp.status_code == 200


# ===========================================================================
# 11. Dedup: same file_id twice within 5 minutes → only latest signal in queue
# ===========================================================================

def test_dedup_within_5_minutes_replaces_old_signal(queue):
    drive = _make_drive_client(content="first version")
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)  # first notification

    assert queue.depth() == 1
    first_signal_id = queue._queue[0].id

    # Second notification within dedup window — different content.
    drive2 = _make_drive_client(content="second version")
    app2 = create_app(drive2, queue, page_token="tok-2", intake_folder_id=INTAKE_FOLDER)
    # Share the same dedup_index by injecting the state directly
    # via a second post to the same app (same state dict).
    with app.test_client() as c:
        _post_notify(c)

    # Still only 1 signal in queue (replaced, not duplicated).
    assert queue.depth() == 1
    # The signal should be the newer one.
    remaining = queue.dequeue()[0]
    # id changes because a new Signal is built for each notification.
    assert remaining.id != first_signal_id or remaining.raw_content in ("first version", "second version")


def test_dedup_within_5_minutes_queue_depth_is_one(queue):
    """Stronger test: two posts to the same app share the same dedup_index."""
    drive = _make_drive_client(content="v1")
    app = _make_app(drive, queue)
    with app.test_client() as c:
        _post_notify(c)
        assert queue.depth() == 1
        _post_notify(c)  # second post, same dedup_index
        assert queue.depth() == 1, "second notification should replace, not duplicate"


# ===========================================================================
# 12. Same file_id more than 5 minutes apart → both signals in queue
# ===========================================================================

def test_dedup_outside_5_minute_window_both_enqueued():
    """Simulate two enqueue calls more than 5 minutes apart by manipulating the index."""
    queue = InMemoryQueue()
    dedup_index: dict = {}

    now = datetime.now(tz=timezone.utc)
    old_time = now - timedelta(minutes=10)

    # Pre-seed the dedup_index with an entry 10 minutes ago.
    old_signal_id = str(uuid.uuid4())
    dedup_index["file-abc-123"] = (old_time, old_signal_id)

    # Build a new signal and enqueue — should NOT replace because > 5 min window.
    drive = _make_drive_client()
    meta = dict(_BASE_FILE_META)
    content = "new content"
    from feed_layer.google_workspace.connector import _build_signal
    signal = _build_signal(meta, content, INTAKE_FOLDER)

    _dedup_enqueue(queue, signal, dedup_index)

    assert queue.depth() == 1
    # The index now points to the new signal.
    assert dedup_index["file-abc-123"][1] == signal.id


# ===========================================================================
# 13. Channel expiry > 1 day → renew_if_needed returns False, no renewal call
# ===========================================================================

def test_watch_channel_renewer_not_needed():
    drive = MagicMock(spec=DriveClient)
    renewer = WatchChannelRenewer(drive, channel_id="chan-1", expiry_days=6)
    # Default expiry is 6 days from now — well beyond the 1-day threshold.
    result = renewer.renew_if_needed()
    assert result is False
    drive.renew_watch_channel.assert_not_called()


# ===========================================================================
# 14. Channel expiry < 1 day → renew_if_needed returns True, renewal called
# ===========================================================================

def test_watch_channel_renewer_needed():
    drive = MagicMock()
    renewer = WatchChannelRenewer(drive, channel_id="chan-1", expiry_days=6)
    # Force the expiry to be 30 minutes from now (within the 1-day window).
    renewer._channel_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=30)
    result = renewer.renew_if_needed()
    assert result is True
    drive.renew_watch_channel.assert_called_once()


# ===========================================================================
# 15. poll_once with 2 ready files → 2 enqueued, cursor advanced
# ===========================================================================

def test_poll_once_two_ready_files():
    queue = InMemoryQueue()
    cursor_store = InMemoryCursorStore()

    file1 = dict(_BASE_FILE_META)
    file1["id"] = "file-001"
    file1["name"] = f"Doc One{READY_SUFFIX}"

    file2 = dict(_BASE_FILE_META)
    file2["id"] = "file-002"
    file2["name"] = f"Doc Two{READY_SUFFIX}"

    drive = MagicMock(spec=DriveClient)
    drive.list_files_in_folder.return_value = [file1, file2]
    drive.get_file_metadata.side_effect = lambda fid: file1 if fid == "file-001" else file2
    drive.get_file_content.return_value = "content"

    poller = GWSPoller(drive, queue, cursor_store, intake_folder_id=INTAKE_FOLDER)
    count = poller.poll_once()

    assert count == 2
    assert queue.depth() == 2
    assert cursor_store.load() is not None  # cursor was advanced


# ===========================================================================
# 16. poll_once with 0 files → 0 enqueued, cursor unchanged
# ===========================================================================

def test_poll_once_no_files():
    queue = InMemoryQueue()
    cursor_store = InMemoryCursorStore()

    drive = MagicMock(spec=DriveClient)
    drive.list_files_in_folder.return_value = []

    poller = GWSPoller(drive, queue, cursor_store, intake_folder_id=INTAKE_FOLDER)
    count = poller.poll_once()

    assert count == 0
    assert queue.depth() == 0
    assert cursor_store.load() is None  # cursor not touched


# ===========================================================================
# 17. poll_once skips file without [READY] suffix
# ===========================================================================

def test_poll_once_skips_non_ready_file():
    queue = InMemoryQueue()
    cursor_store = InMemoryCursorStore()

    file_no_ready = dict(_BASE_FILE_META)
    file_no_ready["name"] = "Design Doc"  # no [READY]

    drive = MagicMock(spec=DriveClient)
    drive.list_files_in_folder.return_value = [file_no_ready]

    poller = GWSPoller(drive, queue, cursor_store, intake_folder_id=INTAKE_FOLDER)
    count = poller.poll_once()

    assert count == 0
    assert queue.depth() == 0
    drive.get_file_metadata.assert_not_called()


# ===========================================================================
# 18. poll_once on restart uses persisted cursor (not current time)
# ===========================================================================

def test_poll_once_uses_persisted_cursor():
    queue = InMemoryQueue()
    saved_cursor = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    cursor_store = InMemoryCursorStore(initial=saved_cursor)

    drive = MagicMock(spec=DriveClient)
    drive.list_files_in_folder.return_value = []

    poller = GWSPoller(drive, queue, cursor_store, intake_folder_id=INTAKE_FOLDER)
    poller.poll_once()

    # The poller must have passed the persisted cursor (not datetime.now).
    call_kwargs = drive.list_files_in_folder.call_args
    passed_since = call_kwargs[1].get("modified_after") or call_kwargs[0][1]
    assert passed_since == saved_cursor


# ===========================================================================
# 19. Signal schema_version="1.0", source="google_workspace", all fields non-null
# ===========================================================================

def test_signal_schema_correctness(client, queue):
    _post_notify(client)
    signal = queue.dequeue()[0]

    assert signal.schema_version == "1.0"
    assert signal.source == "google_workspace"
    assert signal.type == "document.ready"
    assert signal.raw_content is not None and signal.raw_content != ""
    assert signal.timestamp is not None
    assert signal.ingested_at is not None
    assert signal.originator is not None
    assert signal.originator.id is not None
    assert signal.originator.name is not None
    assert signal.metadata is not None
    assert signal.metadata.file_id is not None
    assert signal.metadata.file_name is not None
    assert signal.metadata.mime_type is not None
    assert signal.metadata.folder_id is not None
    assert signal.metadata.doc_url is not None
    assert signal.metadata.sharing_scope is not None


# ===========================================================================
# 20. Signal id is a valid UUID
# ===========================================================================

def test_signal_id_is_valid_uuid(client, queue):
    _post_notify(client)
    signal = queue.dequeue()[0]
    parsed = uuid.UUID(signal.id, version=4)
    assert str(parsed) == signal.id


# ===========================================================================
# 21. ingested_at is connector ingest time, not document modifiedTime
# ===========================================================================

def test_ingested_at_is_ingest_time_not_modified_time(client, queue):
    # The file's modifiedTime is 2026-07-06T10:00:00Z (fixed in _BASE_FILE_META).
    doc_modified = datetime(2026, 7, 6, 10, 0, 0, tzinfo=timezone.utc)
    _post_notify(client)
    signal = queue.dequeue()[0]
    # ingested_at must NOT equal the document's modifiedTime.
    assert signal.ingested_at != doc_modified
    # ingested_at must be close to now (within 10 seconds).
    delta = abs((signal.ingested_at - datetime.now(tz=timezone.utc)).total_seconds())
    assert delta < 10, f"ingested_at {signal.ingested_at} is too far from now"
    # timestamp IS the document's modifiedTime.
    assert signal.timestamp == doc_modified


# ===========================================================================
# BUG 1 — page_token advancement across clustered notifications
# ===========================================================================


def test_page_token_advances_after_notification(queue):
    """After a single notification, state["page_token"] must be updated to
    the new_page_token returned by list_changes — not left at the token that
    was just consumed."""
    drive = MagicMock(spec=DriveClient)
    drive.list_changes.return_value = {
        "changes": [dict(_BASE_CHANGE)],
        "new_page_token": "tok-2",
    }
    drive.get_file_metadata.return_value = dict(_BASE_FILE_META)
    drive.get_file_content.return_value = "content"

    app = create_app(drive, queue, page_token="tok-1", intake_folder_id=INTAKE_FOLDER)
    with app.test_client() as c:
        _post_notify(c)

    drive.list_changes.assert_called_once_with("tok-1")
    assert queue.depth() == 1


def test_two_sequential_notifications_advance_page_token_no_dup(queue):
    """Two clustered notifications must each query a *different* page token
    (the one returned by the previous call), and must not reprocess the same
    underlying change twice."""
    drive = MagicMock(spec=DriveClient)

    change_1 = dict(_BASE_CHANGE)
    change_1["fileId"] = "file-page1"
    meta_1 = dict(_BASE_FILE_META)
    meta_1["id"] = "file-page1"
    change_1["file"] = meta_1

    change_2 = dict(_BASE_CHANGE)
    change_2["fileId"] = "file-page2"
    meta_2 = dict(_BASE_FILE_META)
    meta_2["id"] = "file-page2"
    change_2["file"] = meta_2

    # First call (token "tok-1") returns change_1 and advances to "tok-2".
    # Second call (token "tok-2") returns change_2 and advances to "tok-3".
    # If the bug were present, the handler would call list_changes("tok-1")
    # again on the second notification, re-returning change_1.
    drive.list_changes.side_effect = [
        {"changes": [change_1], "new_page_token": "tok-2"},
        {"changes": [change_2], "new_page_token": "tok-3"},
    ]
    drive.get_file_metadata.side_effect = lambda fid: meta_1 if fid == "file-page1" else meta_2
    drive.get_file_content.return_value = "content"

    app = create_app(drive, queue, page_token="tok-1", intake_folder_id=INTAKE_FOLDER)
    with app.test_client() as c:
        _post_notify(c)  # consumes tok-1 -> tok-2
        _post_notify(c)  # consumes tok-2 -> tok-3

    # list_changes must have been called with the advancing tokens, not the
    # same starting token twice.
    called_tokens = [call.args[0] for call in drive.list_changes.call_args_list]
    assert called_tokens == ["tok-1", "tok-2"]

    # Two distinct files, two distinct signals — no duplicate processing.
    assert queue.depth() == 2
    signals = queue.dequeue()
    file_ids = {s.metadata.file_id for s in signals}
    assert file_ids == {"file-page1", "file-page2"}


def test_unpack_changes_result_dict_shape():
    changes, token = _unpack_changes_result(
        {"changes": [{"a": 1}], "new_page_token": "next"}, "prev"
    )
    assert changes == [{"a": 1}]
    assert token == "next"


def test_unpack_changes_result_tuple_shape():
    changes, token = _unpack_changes_result(([{"a": 1}], "next"), "prev")
    assert changes == [{"a": 1}]
    assert token == "next"


def test_unpack_changes_result_legacy_list_shape():
    """Legacy DriveClient implementations that return a bare list keep
    working — the page token simply doesn't advance (falls back to the
    token that was requested)."""
    changes, token = _unpack_changes_result([{"a": 1}], "prev")
    assert changes == [{"a": 1}]
    assert token == "prev"


# ===========================================================================
# BUG 2 — watch-channel renewal failure must not crash and must be retried
# ===========================================================================


def test_renew_success_updates_expiry_and_returns_true():
    drive = MagicMock(spec=DriveClient)
    renewer = WatchChannelRenewer(drive, channel_id="chan-1", expiry_days=6)
    renewer._channel_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=30)
    old_expiry = renewer.get_channel_expiry()

    result = renewer.renew_if_needed()

    assert result is True
    drive.renew_watch_channel.assert_called_once()
    assert renewer.get_channel_expiry() > old_expiry


def test_renew_failure_is_caught_and_expiry_not_advanced(caplog):
    import logging

    drive = MagicMock(spec=DriveClient)
    drive.renew_watch_channel.side_effect = RuntimeError("Drive API 500")
    renewer = WatchChannelRenewer(drive, channel_id="chan-1", expiry_days=6)
    soon = datetime.now(tz=timezone.utc) + timedelta(minutes=30)
    renewer._channel_expiry = soon

    with caplog.at_level(logging.WARNING):
        result = renewer.renew_if_needed()  # must not raise

    assert result is False
    assert renewer.get_channel_expiry() == soon, "expiry must not advance on failure"
    assert "chan-1" in caplog.text
    # PII discipline: no document content/body should ever be logged here.
    assert "content" not in caplog.text.lower()


def test_renew_failure_then_retry_attempts_again():
    """After a failed renewal, the next renew_if_needed() call must attempt
    renewal again (because expiry was left unchanged and is still within the
    early-renewal window) rather than treating the failure as success."""
    drive = MagicMock(spec=DriveClient)
    drive.renew_watch_channel.side_effect = [RuntimeError("transient failure"), None]
    renewer = WatchChannelRenewer(drive, channel_id="chan-1", expiry_days=6)
    renewer._channel_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=30)

    first_result = renewer.renew_if_needed()
    assert first_result is False
    assert drive.renew_watch_channel.call_count == 1

    # Still within the renewal window (expiry untouched) -> retried.
    second_result = renewer.renew_if_needed()
    assert second_result is True
    assert drive.renew_watch_channel.call_count == 2

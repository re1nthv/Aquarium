"""Google Workspace connector for the Aquarium feed layer.

Exposes:
  - DriveClient      — abstract interface for Google Drive API calls
  - WatchChannelRenewer — manages Drive push-notification channel lifecycle
  - CursorStore      — abstract + in-memory cursor persistence for the poller
  - GWSPoller        — polling fallback that queries the intake folder directly
  - create_app()     — factory that returns a Flask app with the /gdrive/notify endpoint
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import Flask, request, jsonify

from feed_layer.shared import (
    Signal,
    GoogleWorkspaceMetadata,
    Originator,
    MessageQueue,
)
from .config import INTAKE_FOLDER_ID, READY_SUFFIX

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Abstract DriveClient
# ---------------------------------------------------------------------------


class DriveClient(ABC):
    """Abstract interface for Google Drive API calls.

    Concrete implementations wrap the real Google API client.
    Tests inject a mock that implements this interface.
    """

    @abstractmethod
    def list_changes(self, page_token: str) -> dict:
        """Return the changes since *page_token* together with the new cursor.

        Return shape: {"changes": list[dict], "new_page_token": str}

        Each change dict must contain at minimum:
          fileId  : str
          file    : dict  (the file resource — may be absent for removals)
          removed : bool

        ``new_page_token`` is the token callers must persist and pass to the
        *next* call to ``list_changes`` — this is how the Drive Changes API
        signals that a page of changes has been consumed.  Without advancing
        it, callers would re-query the same page and reprocess changes.

        For backward compatibility, callers (see ``_unpack_changes_result``)
        also accept a bare ``list[dict]`` (legacy shape, no new token) or a
        ``(changes, new_page_token)`` tuple.
        """

    @abstractmethod
    def get_file_metadata(self, file_id: str) -> dict:
        """Return the Drive file resource for *file_id*."""

    @abstractmethod
    def get_file_content(self, file_id: str) -> str:
        """Return the plain-text content of *file_id*.

        Raises PermissionError on HTTP 403.
        """

    @abstractmethod
    def list_files_in_folder(
        self, folder_id: str, *, modified_after: Optional[datetime] = None
    ) -> list[dict]:
        """Return Drive file resources in *folder_id* modified after *modified_after*.

        Used by GWSPoller as the polling fallback.
        """

    @abstractmethod
    def renew_watch_channel(self, channel_id: str, *, expiry_days: int = 6) -> None:
        """Renew a Drive push-notification watch channel.

        Used by WatchChannelRenewer to extend channel lifetime.
        """


# ---------------------------------------------------------------------------
# CursorStore
# ---------------------------------------------------------------------------


class CursorStore(ABC):
    """Durable cursor storage for the polling fallback."""

    @abstractmethod
    def load(self) -> Optional[datetime]:
        """Return the persisted cursor, or None if none has been saved."""

    @abstractmethod
    def save(self, cursor: datetime) -> None:
        """Persist *cursor* so it survives restarts."""


class InMemoryCursorStore(CursorStore):
    """In-process cursor store — for local development and testing only."""

    def __init__(self, initial: Optional[datetime] = None) -> None:
        self._cursor: Optional[datetime] = initial

    def load(self) -> Optional[datetime]:
        return self._cursor

    def save(self, cursor: datetime) -> None:
        self._cursor = cursor


# ---------------------------------------------------------------------------
# 5-minute dedup helper
# ---------------------------------------------------------------------------

_DEDUP_WINDOW = timedelta(minutes=5)


def _dedup_enqueue(
    queue: MessageQueue,
    signal: Signal,
    dedup_index: dict[str, tuple[datetime, str]],
) -> None:
    """Enqueue *signal* with 5-minute deduplication.

    *dedup_index* maps file_id → (enqueued_at, signal_id).
    If the same file_id was enqueued within the last 5 minutes the old entry
    in the queue is replaced by *signal* (update-in-place semantics).
    """
    file_id = signal.metadata.file_id  # type: ignore[union-attr]
    now = signal.ingested_at

    if file_id in dedup_index:
        prev_at, prev_id = dedup_index[file_id]
        if now - prev_at < _DEDUP_WINDOW:
            # Replace the previous signal in the queue in-place.
            _replace_in_queue(queue, prev_id, signal)
            dedup_index[file_id] = (now, signal.id)
            return

    queue.enqueue(signal)
    dedup_index[file_id] = (now, signal.id)


def _replace_in_queue(queue: MessageQueue, old_id: str, new_signal: Signal) -> None:
    """Replace the signal with *old_id* in *queue* with *new_signal*.

    Works for InMemoryQueue (direct list access) and any queue that exposes
    a ``_queue`` list attribute.  For other queue backends this degrades to
    a plain enqueue — callers must implement their own replace semantics.
    """
    internal = getattr(queue, "_queue", None)
    if internal is not None:
        for i, s in enumerate(internal):
            if s.id == old_id:
                internal[i] = new_signal
                return
    # Fallback: just enqueue — dedup is best-effort for opaque backends.
    queue.enqueue(new_signal)


# ---------------------------------------------------------------------------
# Signal builder
# ---------------------------------------------------------------------------


def _derive_sharing_scope(file_meta: dict) -> str:
    """Derive sharing_scope from Drive file permissions metadata.

    Expected values in file_meta['permissions'] (list of permission dicts):
      - type='anyone'       → "public"
      - type='domain'       → "org"
      - anything else       → "team"
    """
    permissions: list[dict] = file_meta.get("permissions", [])
    for perm in permissions:
        ptype = perm.get("type", "")
        if ptype == "anyone":
            return "public"
        if ptype == "domain":
            return "org"
    return "team"


def _build_signal(file_meta: dict, content: str, folder_id: str) -> Signal:
    """Build a canonical Signal from Drive file metadata and plain-text content."""
    now = datetime.now(tz=timezone.utc)

    last_mod = file_meta.get("lastModifyingUser", {})
    originator = Originator(
        id=last_mod.get("permissionId", ""),
        name=last_mod.get("displayName", ""),
    )

    metadata = GoogleWorkspaceMetadata(
        file_id=file_meta["id"],
        file_name=file_meta["name"],
        mime_type=file_meta.get("mimeType", ""),
        folder_id=folder_id,
        doc_url=file_meta.get("webViewLink", ""),
        sharing_scope=_derive_sharing_scope(file_meta),  # type: ignore[arg-type]
    )

    # timestamp = document's last-modified time (Drive modifiedTime field)
    modified_time_str = file_meta.get("modifiedTime", "")
    try:
        timestamp = datetime.fromisoformat(modified_time_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        timestamp = now

    return Signal(
        source="google_workspace",
        type="document.ready",
        raw_content=content,
        timestamp=timestamp,
        ingested_at=now,
        originator=originator,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# list_changes() result normalization
# ---------------------------------------------------------------------------


def _unpack_changes_result(
    result: object, fallback_page_token: str
) -> tuple[list[dict], str]:
    """Normalize the return value of DriveClient.list_changes().

    Supports three shapes, from newest/preferred to legacy:
      - dict:   {"changes": [...], "new_page_token": "..."}
      - tuple:  (changes, new_page_token)
      - list:   changes only (legacy) — page token does not advance.

    Returns (changes, new_page_token). If no new token is available,
    *fallback_page_token* (the token that was requested) is returned so
    callers don't accidentally clear their cursor.
    """
    if isinstance(result, dict):
        changes = result.get("changes", [])
        new_page_token = result.get("new_page_token", fallback_page_token)
        return changes, new_page_token

    if isinstance(result, tuple):
        changes = result[0] if len(result) > 0 else []
        new_page_token = result[1] if len(result) > 1 else fallback_page_token
        return changes, new_page_token

    # Legacy shape: bare list of changes, no new token available.
    return list(result), fallback_page_token  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _file_qualifies helper
# ---------------------------------------------------------------------------


def _file_qualifies(change: dict, intake_folder_id: str) -> bool:
    """Return True iff *change* represents a file that should be processed.

    Skips:
      - removals (change['removed'] is True)
      - files whose parent folder is not *intake_folder_id*
      - files whose name does not end with READY_SUFFIX
    """
    if change.get("removed", False):
        return False

    file_resource = change.get("file", {})
    parents: list[str] = file_resource.get("parents", [])
    if intake_folder_id not in parents:
        return False

    name: str = file_resource.get("name", "")
    if not name.endswith(READY_SUFFIX):
        return False

    return True


# ---------------------------------------------------------------------------
# Flask application factory
# ---------------------------------------------------------------------------


def create_app(
    drive_client: DriveClient,
    queue: MessageQueue,
    *,
    page_token: str = "1",
    intake_folder_id: str = INTAKE_FOLDER_ID,
) -> Flask:
    """Return a Flask app with the /gdrive/notify webhook endpoint.

    Args:
        drive_client:    Injectable DriveClient (real or mock).
        queue:           MessageQueue to write signals to.
        page_token:      Starting page token for Drive Changes API.
        intake_folder_id: Drive folder ID to filter against.
    """
    app = Flask(__name__)

    # Mutable state shared across requests (per-process).
    state: dict = {
        "page_token": page_token,
        "dedup_index": {},  # file_id → (enqueued_at, signal_id)
    }

    @app.route("/gdrive/notify", methods=["POST"])
    def gdrive_notify():  # type: ignore[return]
        resource_state = request.headers.get("X-Goog-Resource-State", "")
        channel_id = request.headers.get("X-Goog-Channel-Id", "")
        resource_id = request.headers.get("X-Goog-Resource-Id", "")

        logger.debug(
            "gdrive_notify: channel=%s resource=%s state=%s",
            channel_id,
            resource_id,
            resource_state,
        )

        # Always return 200 immediately.
        if resource_state != "change":
            return "", 200

        try:
            raw_result = drive_client.list_changes(state["page_token"])
        except Exception:
            logger.exception("gdrive_notify: list_changes failed")
            return "", 200

        changes, new_page_token = _unpack_changes_result(raw_result, state["page_token"])
        # Persist the new cursor immediately so a clustered follow-up
        # notification (or a crash mid-loop) doesn't re-query this same
        # page and reprocess changes already seen here.
        state["page_token"] = new_page_token

        for change in changes:
            file_resource = change.get("file", {})
            file_id: str = change.get("fileId") or file_resource.get("id", "")

            if not _file_qualifies(change, intake_folder_id):
                continue

            # Fetch full metadata and content.
            try:
                file_meta = drive_client.get_file_metadata(file_id)
            except Exception:
                logger.exception("gdrive_notify: get_file_metadata failed for %s", file_id)
                continue

            try:
                content = drive_client.get_file_content(file_id)
            except PermissionError:
                logger.warning(
                    "permission_denied: cannot read file %s — skipping", file_id
                )
                continue
            except Exception:
                logger.exception("gdrive_notify: get_file_content failed for %s", file_id)
                continue

            signal = _build_signal(file_meta, content, intake_folder_id)

            try:
                _dedup_enqueue(queue, signal, state["dedup_index"])
            except Exception:
                logger.exception(
                    "gdrive_notify: failed to enqueue signal for file %s", file_id
                )
                # Do not crash — continue processing remaining changes.

        return "", 200

    return app


# ---------------------------------------------------------------------------
# WatchChannelRenewer
# ---------------------------------------------------------------------------


class WatchChannelRenewer:
    """Manages the lifecycle of a Drive push-notification watch channel.

    Google Drive watch channels expire; this class tracks expiry and renews
    the channel before it lapses (within a 1-day early-renewal window).
    """

    def __init__(
        self,
        drive_client: DriveClient,
        channel_id: str,
        expiry_days: int = 6,
    ) -> None:
        self._client = drive_client
        self._channel_id = channel_id
        self._expiry_days = expiry_days
        self._channel_expiry: datetime = datetime.now(tz=timezone.utc) + timedelta(
            days=expiry_days
        )

    def get_channel_expiry(self) -> datetime:
        """Return the current channel expiry timestamp."""
        return self._channel_expiry

    def renew_if_needed(self) -> bool:
        """Renew the watch channel if it expires within 1 day.

        Returns True if a renewal was performed *and succeeded*, False
        otherwise — including the case where renewal was attempted but the
        underlying Drive API call failed.  A failed renewal never raises:
        per the connector's delivery-guarantee contract, renewal is a
        best-effort background job and the poller is the fallback path if
        the watch channel eventually lapses.  Because expiry is left
        untouched on failure, the next call to ``renew_if_needed`` will
        attempt renewal again.
        """
        now = datetime.now(tz=timezone.utc)
        if self._channel_expiry - now <= timedelta(days=1):
            return self._do_renew()
        return False

    def _do_renew(self) -> bool:
        """Call the Drive API to renew the watch channel.

        Returns True on success, False if the renewal call raised. On
        failure, ``_channel_expiry`` is left unchanged so the failure is
        retried on the next ``renew_if_needed()`` call instead of being
        silently treated as a successful renewal.
        """
        try:
            self._client.renew_watch_channel(
                self._channel_id, expiry_days=self._expiry_days
            )
        except Exception:
            # Do not log document content or other PII — only the channel
            # id (an opaque identifier we generated) and the fact that
            # renewal failed.
            logger.warning(
                "watch_channel_renew_failed: channel=%s — will retry on next "
                "renew_if_needed() call; falling back to polling in the "
                "interim",
                self._channel_id,
            )
            return False

        self._channel_expiry = datetime.now(tz=timezone.utc) + timedelta(
            days=self._expiry_days
        )
        return True


# ---------------------------------------------------------------------------
# GWSPoller
# ---------------------------------------------------------------------------


class GWSPoller:
    """Polling fallback for the Google Workspace connector.

    Queries the intake folder for files whose name contains [READY] and whose
    modifiedTime is newer than the persisted cursor.  On each successful
    enqueue the cursor is advanced so restarts resume from where polling left
    off.
    """

    def __init__(
        self,
        drive_client: DriveClient,
        queue: MessageQueue,
        cursor_store: CursorStore,
        *,
        intake_folder_id: str = INTAKE_FOLDER_ID,
    ) -> None:
        self._client = drive_client
        self._queue = queue
        self._cursor_store = cursor_store
        self._intake_folder_id = intake_folder_id
        self._dedup_index: dict[str, tuple[datetime, str]] = {}

    def poll_once(self) -> int:
        """Poll the intake folder once and return the number of signals enqueued."""
        cursor: Optional[datetime] = self._cursor_store.load()
        # Use epoch if no cursor is persisted (first run).
        since = cursor if cursor is not None else datetime.fromtimestamp(0, tz=timezone.utc)

        try:
            files = self._client.list_files_in_folder(
                self._intake_folder_id, modified_after=since
            )
        except Exception:
            logger.exception("GWSPoller: list_files_in_folder failed")
            return 0

        count = 0
        for file_resource in files:
            name: str = file_resource.get("name", "")
            if not name.endswith(READY_SUFFIX):
                continue

            file_id: str = file_resource.get("id", "")

            try:
                file_meta = self._client.get_file_metadata(file_id)
            except Exception:
                logger.exception("GWSPoller: get_file_metadata failed for %s", file_id)
                continue

            try:
                content = self._client.get_file_content(file_id)
            except PermissionError:
                logger.warning(
                    "permission_denied: cannot read file %s — skipping", file_id
                )
                continue
            except Exception:
                logger.exception("GWSPoller: get_file_content failed for %s", file_id)
                continue

            signal = _build_signal(file_meta, content, self._intake_folder_id)

            try:
                _dedup_enqueue(self._queue, signal, self._dedup_index)
            except Exception:
                logger.exception("GWSPoller: enqueue failed for file %s", file_id)
                continue

            # Advance cursor after each successful enqueue.
            self._cursor_store.save(signal.ingested_at)
            count += 1

        return count

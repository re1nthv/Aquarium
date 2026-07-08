"""Signal observer hook for the classifier.

The classifier stays decoupled from the dashboard: it emits lifecycle events to
an injected ``SignalObserver``. The default ``NullObserver`` does nothing (so
existing wiring is unchanged), while ``JournalObserver`` forwards each event to
a ``SignalJournal`` for the dashboard to read.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from feed_layer.shared.signal import (
    GoogleWorkspaceMetadata,
    GusMetadata,
    Signal,
    SlackMetadata,
)

from .store import SignalJournal


def _context_for(signal: Signal) -> dict:
    """Extract UI-friendly source detail from a signal's metadata."""
    meta = signal.metadata
    if isinstance(meta, SlackMetadata):
        return {
            "channel_id": meta.channel_id,
            "message_ts": meta.message_ts,
            "bot_name": meta.bot_name,
        }
    if isinstance(meta, GusMetadata):
        return {
            "team": meta.team,
            "item_type": meta.gus_item_type,
            "item_url": meta.gus_item_url,
            "release": meta.release,
        }
    if isinstance(meta, GoogleWorkspaceMetadata):
        return {
            "file_name": meta.file_name,
            "doc_url": meta.doc_url,
            "sharing_scope": meta.sharing_scope,
        }
    return {}


class SignalObserver(ABC):
    """Lifecycle callbacks invoked by the classifier as it processes a signal."""

    @abstractmethod
    def on_detected(self, signal: Signal) -> None: ...

    @abstractmethod
    def on_dropped(self, signal: Signal, rule: Optional[str]) -> None: ...

    @abstractmethod
    def on_duplicate(self, signal: Signal, canonical_id: Optional[str]) -> None: ...

    @abstractmethod
    def on_parked(self, signal: Signal, reason: str) -> None: ...

    @abstractmethod
    def on_review(self, signal: Signal, reason: str) -> None: ...

    @abstractmethod
    def on_work_item(self, signal: Signal, confidence: Optional[float]) -> None: ...


class NullObserver(SignalObserver):
    """Default no-op observer — keeps the classifier's behaviour unchanged."""

    def on_detected(self, signal: Signal) -> None: ...
    def on_dropped(self, signal: Signal, rule: Optional[str]) -> None: ...
    def on_duplicate(self, signal: Signal, canonical_id: Optional[str]) -> None: ...
    def on_parked(self, signal: Signal, reason: str) -> None: ...
    def on_review(self, signal: Signal, reason: str) -> None: ...
    def on_work_item(self, signal: Signal, confidence: Optional[float]) -> None: ...


class JournalObserver(SignalObserver):
    """Writes classifier lifecycle events into a ``SignalJournal``."""

    def __init__(self, journal: SignalJournal) -> None:
        self._journal = journal

    def on_detected(self, signal: Signal) -> None:
        self._journal.record_detected(
            signal_id=signal.id,
            source=signal.source,
            type=signal.type,
            originator_name=getattr(signal.originator, "name", "") or "",
            raw_content=signal.raw_content,
            detected_at=signal.ingested_at,
            context=_context_for(signal),
        )

    def on_dropped(self, signal: Signal, rule: Optional[str]) -> None:
        self._journal.record_outcome(
            signal.id, "dropped", pre_filter_rule=rule
        )

    def on_duplicate(self, signal: Signal, canonical_id: Optional[str]) -> None:
        self._journal.record_outcome(
            signal.id, "duplicate", duplicate_of=canonical_id
        )

    def on_parked(self, signal: Signal, reason: str) -> None:
        self._journal.record_outcome(signal.id, "parked", review_reason=reason)

    def on_review(self, signal: Signal, reason: str) -> None:
        self._journal.record_outcome(signal.id, "review", review_reason=reason)

    def on_work_item(self, signal: Signal, confidence: Optional[float]) -> None:
        self._journal.record_outcome(
            signal.id, "work_item", confidence=confidence
        )

"""Control-plane state for the feed layer.

Two runtime stores that connectors, the classifier, and the dashboard all
share:

* ``ControlStore`` — editable configuration (which Slack channels to sniff,
  which GUS teams to follow). Persisted to a JSON file with an atomic write so
  a restart resumes the operator's choices. Every filter is fail-open: an empty
  configuration allows everything, so wiring the store into a connector never
  changes behaviour until an operator narrows the scope from the dashboard.

* ``SignalJournal`` — an in-memory observability record of every signal the
  pipeline sees, from detection through to its terminal outcome. The dashboard
  reads it to show the live feed, the work-item list, and rollup counts.

Both are safe to share across threads (a webhook handler and a poller can write
concurrently while the dashboard reads).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

# The terminal outcome of a signal once the classifier has routed it.
#   work_item  — classified relevant, forwarded downstream as a work item
#   dropped    — classified irrelevant, or dropped by a pre-filter rule
#   duplicate  — collapsed into an earlier signal by semantic dedup
#   review     — classified uncertain, sent to the human review queue
#   parked     — deferred (budget exhausted / LLM unavailable / stale), retried
#   detected   — seen but not yet routed (transient, pre-outcome)
Outcome = Literal[
    "detected", "work_item", "dropped", "duplicate", "review", "parked"
]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SNIPPET_CHARS = 280


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# ControlStore — editable configuration
# ---------------------------------------------------------------------------


@dataclass
class SlackChannel:
    """A Slack channel the operator may toggle for sniffing."""

    id: str
    name: str = ""
    enabled: bool = True


class ControlStore:
    """Editable feed-layer configuration, optionally persisted to JSON.

    Thread-safe. When ``path`` is given the state is loaded on construction and
    written atomically after every mutation. A missing or malformed file yields
    empty defaults rather than raising.
    """

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = path
        self._lock = threading.RLock()
        # Slack channels keyed by id for O(1) toggles.
        self._slack_channels: dict[str, SlackChannel] = {}
        # GUS teams the operator follows (case-insensitive membership) plus a
        # map back to the original-case name for display.
        self._gus_teams: set[str] = set()
        self._team_display: dict[str, str] = {}
        # Google Workspace intake config (display-only for now).
        self._gws_folder_id: str = ""
        self._gws_ready_suffix: str = " [READY]"
        if path:
            self._load()

    # ---- Slack channels --------------------------------------------------

    def add_slack_channel(
        self, channel_id: str, name: str = "", enabled: bool = True
    ) -> SlackChannel:
        """Register a channel (or update its name), returning the stored entry."""
        with self._lock:
            existing = self._slack_channels.get(channel_id)
            if existing is not None:
                if name:
                    existing.name = name
                channel = existing
            else:
                channel = SlackChannel(id=channel_id, name=name, enabled=enabled)
                self._slack_channels[channel_id] = channel
            self._save()
            return channel

    def set_slack_channel_enabled(
        self, channel_id: str, enabled: bool
    ) -> SlackChannel:
        """Toggle sniffing for a channel, auto-registering it if unknown."""
        with self._lock:
            channel = self._slack_channels.get(channel_id)
            if channel is None:
                channel = SlackChannel(id=channel_id, enabled=enabled)
                self._slack_channels[channel_id] = channel
            else:
                channel.enabled = enabled
            self._save()
            return channel

    def remove_slack_channel(self, channel_id: str) -> None:
        with self._lock:
            if self._slack_channels.pop(channel_id, None) is not None:
                self._save()

    def slack_channels(self) -> list[SlackChannel]:
        with self._lock:
            return [
                SlackChannel(c.id, c.name, c.enabled)
                for c in self._slack_channels.values()
            ]

    def slack_channel_enabled(self, channel_id: str) -> bool:
        """Return True if this channel should be sniffed.

        Fail-open: when no channels are configured at all, every channel is
        allowed. Once the operator has registered any channel, only channels
        explicitly present *and* enabled pass.
        """
        with self._lock:
            if not self._slack_channels:
                return True
            channel = self._slack_channels.get(channel_id)
            if channel is None:
                return False
            return channel.enabled

    # ---- GUS teams -------------------------------------------------------

    def add_gus_team(self, team: str) -> None:
        team = team.strip()
        if not team:
            return
        with self._lock:
            key = team.lower()
            if key not in self._gus_teams:
                self._gus_teams.add(key)
                self._team_display[key] = team
                self._save()

    def remove_gus_team(self, team: str) -> None:
        with self._lock:
            key = team.strip().lower()
            if key in self._gus_teams:
                self._gus_teams.discard(key)
                self._team_display.pop(key, None)
                self._save()

    def gus_teams(self) -> list[str]:
        with self._lock:
            return [self._team_display[k] for k in sorted(self._gus_teams)]

    def gus_team_followed(self, team: Optional[str]) -> bool:
        """Return True if items for *team* should be ingested.

        Fail-open: when no teams are configured, every team is followed.
        """
        with self._lock:
            if not self._gus_teams:
                return True
            if not team:
                return False
            return team.strip().lower() in self._gus_teams

    # ---- Google Workspace (display-only) ---------------------------------

    def gws_config(self) -> dict:
        with self._lock:
            return {
                "folder_id": self._gws_folder_id,
                "ready_suffix": self._gws_ready_suffix,
            }

    def set_gws_config(
        self,
        folder_id: Optional[str] = None,
        ready_suffix: Optional[str] = None,
    ) -> None:
        with self._lock:
            if folder_id is not None:
                self._gws_folder_id = folder_id
            if ready_suffix is not None:
                self._gws_ready_suffix = ready_suffix
            self._save()

    # ---- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "slack_channels": [
                    asdict(c) for c in self._slack_channels.values()
                ],
                "gus_teams": [self._team_display[k] for k in sorted(self._gus_teams)],
                "gws": {
                    "folder_id": self._gws_folder_id,
                    "ready_suffix": self._gws_ready_suffix,
                },
            }

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (FileNotFoundError, ValueError, OSError):
            return
        if not isinstance(data, dict):
            return
        for raw in data.get("slack_channels", []) or []:
            try:
                self._slack_channels[raw["id"]] = SlackChannel(
                    id=raw["id"],
                    name=raw.get("name", ""),
                    enabled=bool(raw.get("enabled", True)),
                )
            except (KeyError, TypeError):
                continue
        for team in data.get("gus_teams", []) or []:
            if isinstance(team, str) and team.strip():
                key = team.strip().lower()
                self._gus_teams.add(key)
                self._team_display[key] = team.strip()
        gws = data.get("gws", {}) or {}
        if isinstance(gws, dict):
            self._gws_folder_id = gws.get("folder_id", "")
            self._gws_ready_suffix = gws.get("ready_suffix", " [READY]")

    def _save(self) -> None:
        # Caller already holds the lock.
        if not self._path:
            return
        tmp = f"{self._path}.tmp"
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._path)


# ---------------------------------------------------------------------------
# SignalJournal — observability record
# ---------------------------------------------------------------------------


@dataclass
class JournalEntry:
    """One signal's journey through the feed layer, updated in place."""

    signal_id: str
    source: str
    type: str
    originator_name: str
    snippet: str
    detected_at: str
    outcome: Outcome = "detected"
    outcome_at: Optional[str] = None
    # Populated when the signal reaches a terminal state.
    duplicate_of: Optional[str] = None
    review_reason: Optional[str] = None
    pre_filter_rule: Optional[str] = None
    confidence: Optional[float] = None
    # Loosely-typed source detail for the UI (channel name, team, doc, ...).
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class SignalJournal:
    """In-memory, thread-safe record of detected signals and their outcomes.

    A bounded ring (``max_entries``) keeps memory flat in a long-running
    process; the newest entries are always retained.
    """

    # Outcomes that mean "this became a work item to consider downstream".
    WORK_ITEM_OUTCOMES = ("work_item",)

    def __init__(self, max_entries: int = 2000) -> None:
        self._lock = threading.RLock()
        self._entries: dict[str, JournalEntry] = {}
        self._order: list[str] = []  # insertion order of signal ids
        self._max = max_entries

    def record_detected(
        self,
        *,
        signal_id: str,
        source: str,
        type: str,
        originator_name: str = "",
        raw_content: str = "",
        detected_at: Optional[datetime] = None,
        context: Optional[dict] = None,
    ) -> JournalEntry:
        """Log a freshly-detected signal (idempotent on ``signal_id``)."""
        with self._lock:
            existing = self._entries.get(signal_id)
            if existing is not None:
                return existing
            entry = JournalEntry(
                signal_id=signal_id,
                source=source,
                type=type,
                originator_name=originator_name,
                snippet=(raw_content or "")[:_SNIPPET_CHARS],
                detected_at=_iso(detected_at or _now()),
                context=dict(context or {}),
            )
            self._entries[signal_id] = entry
            self._order.append(signal_id)
            self._evict_if_needed()
            return entry

    def record_outcome(
        self,
        signal_id: str,
        outcome: Outcome,
        *,
        duplicate_of: Optional[str] = None,
        review_reason: Optional[str] = None,
        pre_filter_rule: Optional[str] = None,
        confidence: Optional[float] = None,
        at: Optional[datetime] = None,
    ) -> Optional[JournalEntry]:
        """Set the terminal outcome for a previously-detected signal.

        No-op returning ``None`` if the signal was never detected (the journal
        never fabricates entries from an outcome alone).
        """
        with self._lock:
            entry = self._entries.get(signal_id)
            if entry is None:
                return None
            entry.outcome = outcome
            entry.outcome_at = _iso(at or _now())
            if duplicate_of is not None:
                entry.duplicate_of = duplicate_of
            if review_reason is not None:
                entry.review_reason = review_reason
            if pre_filter_rule is not None:
                entry.pre_filter_rule = pre_filter_rule
            if confidence is not None:
                entry.confidence = confidence
            return entry

    # ---- queries ---------------------------------------------------------

    def recent(
        self,
        limit: int = 100,
        *,
        source: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> list[JournalEntry]:
        """Most-recent-first entries, optionally filtered by source/outcome."""
        with self._lock:
            ids = reversed(self._order)
            out: list[JournalEntry] = []
            for sid in ids:
                entry = self._entries.get(sid)
                if entry is None:
                    continue
                if source and entry.source != source:
                    continue
                if outcome and entry.outcome != outcome:
                    continue
                out.append(entry)
                if len(out) >= limit:
                    break
            return out

    def work_items(self, limit: int = 100) -> list[JournalEntry]:
        with self._lock:
            out: list[JournalEntry] = []
            for sid in reversed(self._order):
                entry = self._entries.get(sid)
                if entry is not None and entry.outcome in self.WORK_ITEM_OUTCOMES:
                    out.append(entry)
                    if len(out) >= limit:
                        break
            return out

    def counts(self) -> dict:
        """Rollup of totals by source and by outcome, for status cards."""
        with self._lock:
            by_source: dict[str, int] = {}
            by_outcome: dict[str, int] = {}
            last_detected: dict[str, str] = {}
            for sid in self._order:
                entry = self._entries.get(sid)
                if entry is None:
                    continue
                by_source[entry.source] = by_source.get(entry.source, 0) + 1
                by_outcome[entry.outcome] = by_outcome.get(entry.outcome, 0) + 1
                # _order is insertion order, so the last write per source wins.
                last_detected[entry.source] = entry.detected_at
            return {
                "total": len(self._entries),
                "by_source": by_source,
                "by_outcome": by_outcome,
                "last_detected": last_detected,
            }

    def _evict_if_needed(self) -> None:
        # Caller holds the lock.
        while len(self._order) > self._max:
            oldest = self._order.pop(0)
            self._entries.pop(oldest, None)

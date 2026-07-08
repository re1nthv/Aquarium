"""Tests for the feed-layer control plane (ControlStore + SignalJournal)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feed_layer.control_plane import (
    ControlStore,
    JournalObserver,
    NullObserver,
    SignalJournal,
)
from feed_layer.shared.signal import (
    GusMetadata,
    Originator,
    Signal,
    SlackMetadata,
)


def _now():
    return datetime.now(tz=timezone.utc)


def _slack_signal(sid="s1", channel="C1", content="a real message here"):
    return Signal(
        source="slack",
        type="manual_reaction",
        raw_content=content,
        timestamp=_now(),
        ingested_at=_now(),
        originator=Originator(id="U1", name="Alice"),
        metadata=SlackMetadata(channel_id=channel, message_ts="123.45"),
        id=sid,
    )


def _gus_signal(sid="g1", team="Falcon"):
    return Signal(
        source="gus",
        type="epic.created",
        raw_content="Epic: ship the thing",
        timestamp=_now(),
        ingested_at=_now(),
        originator=Originator(id="U2", name="Bob"),
        metadata=GusMetadata(
            gus_item_id="E-1",
            gus_item_type="epic",
            gus_item_url="http://gus/E-1",
            team=team,
        ),
        id=sid,
    )


# ---------------------------------------------------------------------------
# ControlStore — Slack channels
# ---------------------------------------------------------------------------


class TestSlackChannelToggles:
    def test_no_channels_configured_allows_all(self):
        store = ControlStore()
        assert store.slack_channel_enabled("C_ANY") is True

    def test_registered_enabled_channel_allowed(self):
        store = ControlStore()
        store.add_slack_channel("C1", name="general", enabled=True)
        assert store.slack_channel_enabled("C1") is True

    def test_registered_but_disabled_channel_blocked(self):
        store = ControlStore()
        store.add_slack_channel("C1", name="general")
        store.set_slack_channel_enabled("C1", False)
        assert store.slack_channel_enabled("C1") is False

    def test_unregistered_channel_blocked_once_any_exist(self):
        store = ControlStore()
        store.add_slack_channel("C1", name="general")
        # A different channel is now implicitly out of scope.
        assert store.slack_channel_enabled("C2") is False

    def test_toggle_auto_registers_unknown_channel(self):
        store = ControlStore()
        ch = store.set_slack_channel_enabled("C9", True)
        assert ch.id == "C9" and ch.enabled is True
        assert any(c.id == "C9" for c in store.slack_channels())

    def test_remove_channel(self):
        store = ControlStore()
        store.add_slack_channel("C1")
        store.remove_slack_channel("C1")
        assert store.slack_channels() == []
        # Back to fail-open with nothing configured.
        assert store.slack_channel_enabled("C1") is True


# ---------------------------------------------------------------------------
# ControlStore — GUS teams
# ---------------------------------------------------------------------------


class TestGusTeamFollowing:
    def test_no_teams_configured_follows_all(self):
        store = ControlStore()
        assert store.gus_team_followed("AnyTeam") is True

    def test_followed_team_matches_case_insensitively(self):
        store = ControlStore()
        store.add_gus_team("Falcon")
        assert store.gus_team_followed("falcon") is True
        assert store.gus_team_followed("FALCON") is True

    def test_unfollowed_team_blocked(self):
        store = ControlStore()
        store.add_gus_team("Falcon")
        assert store.gus_team_followed("Eagle") is False

    def test_none_team_blocked_when_teams_configured(self):
        store = ControlStore()
        store.add_gus_team("Falcon")
        assert store.gus_team_followed(None) is False

    def test_teams_preserve_display_case(self):
        store = ControlStore()
        store.add_gus_team("Falcon")
        assert store.gus_teams() == ["Falcon"]

    def test_remove_team(self):
        store = ControlStore()
        store.add_gus_team("Falcon")
        store.remove_gus_team("falcon")
        assert store.gus_teams() == []
        assert store.gus_team_followed("Falcon") is True


# ---------------------------------------------------------------------------
# ControlStore — persistence
# ---------------------------------------------------------------------------


class TestControlStorePersistence:
    def test_round_trip_across_instances(self, tmp_path):
        path = str(tmp_path / "control.json")
        store = ControlStore(path=path)
        store.add_slack_channel("C1", name="general", enabled=False)
        store.add_gus_team("Falcon")

        reloaded = ControlStore(path=path)
        channels = reloaded.slack_channels()
        assert len(channels) == 1
        assert channels[0].id == "C1" and channels[0].enabled is False
        assert reloaded.gus_teams() == ["Falcon"]

    def test_missing_file_defaults_empty(self, tmp_path):
        store = ControlStore(path=str(tmp_path / "nope.json"))
        assert store.slack_channels() == []
        assert store.gus_teams() == []

    def test_malformed_file_defaults_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        store = ControlStore(path=str(path))
        assert store.slack_channels() == []

    def test_atomic_write_leaves_no_tmp(self, tmp_path):
        path = tmp_path / "control.json"
        store = ControlStore(path=str(path))
        store.add_gus_team("Falcon")
        assert not (tmp_path / "control.json.tmp").exists()
        assert path.exists()


# ---------------------------------------------------------------------------
# SignalJournal
# ---------------------------------------------------------------------------


class TestSignalJournal:
    def test_record_detected_then_outcome(self):
        j = SignalJournal()
        j.record_detected(
            signal_id="s1", source="slack", type="manual_reaction",
            originator_name="Alice", raw_content="hello world here",
        )
        j.record_outcome("s1", "work_item", confidence=0.91)
        entry = j.recent()[0]
        assert entry.signal_id == "s1"
        assert entry.outcome == "work_item"
        assert entry.confidence == 0.91
        assert entry.outcome_at is not None

    def test_snippet_truncated(self):
        j = SignalJournal()
        long = "x" * 1000
        j.record_detected(
            signal_id="s1", source="slack", type="bot_message",
            raw_content=long,
        )
        assert len(j.recent()[0].snippet) <= 280

    def test_detected_is_idempotent(self):
        j = SignalJournal()
        j.record_detected(signal_id="s1", source="slack", type="x", raw_content="aaaaaaaaaa")
        j.record_detected(signal_id="s1", source="slack", type="x", raw_content="different")
        assert len(j.recent()) == 1
        assert j.recent()[0].snippet == "aaaaaaaaaa"

    def test_outcome_without_detected_is_noop(self):
        j = SignalJournal()
        assert j.record_outcome("ghost", "work_item") is None
        assert j.recent() == []

    def test_recent_is_newest_first(self):
        j = SignalJournal()
        for i in range(3):
            j.record_detected(signal_id=f"s{i}", source="slack", type="x", raw_content="content ok")
        assert [e.signal_id for e in j.recent()] == ["s2", "s1", "s0"]

    def test_recent_filters_by_source_and_outcome(self):
        j = SignalJournal()
        j.record_detected(signal_id="s1", source="slack", type="x", raw_content="content ok")
        j.record_detected(signal_id="g1", source="gus", type="epic.created", raw_content="content ok")
        j.record_outcome("s1", "work_item")
        j.record_outcome("g1", "dropped")
        assert [e.signal_id for e in j.recent(source="slack")] == ["s1"]
        assert [e.signal_id for e in j.recent(outcome="dropped")] == ["g1"]

    def test_work_items_only_returns_work_items(self):
        j = SignalJournal()
        j.record_detected(signal_id="s1", source="slack", type="x", raw_content="content ok")
        j.record_detected(signal_id="s2", source="slack", type="x", raw_content="content ok")
        j.record_outcome("s1", "work_item")
        j.record_outcome("s2", "dropped")
        assert [e.signal_id for e in j.work_items()] == ["s1"]

    def test_counts_rollup(self):
        j = SignalJournal()
        j.record_detected(signal_id="s1", source="slack", type="x", raw_content="content ok")
        j.record_detected(signal_id="g1", source="gus", type="epic.created", raw_content="content ok")
        j.record_outcome("s1", "work_item")
        j.record_outcome("g1", "work_item")
        counts = j.counts()
        assert counts["total"] == 2
        assert counts["by_source"] == {"slack": 1, "gus": 1}
        assert counts["by_outcome"]["work_item"] == 2

    def test_ring_buffer_evicts_oldest(self):
        j = SignalJournal(max_entries=2)
        for i in range(3):
            j.record_detected(signal_id=f"s{i}", source="slack", type="x", raw_content="content ok")
        ids = {e.signal_id for e in j.recent()}
        assert ids == {"s1", "s2"}  # s0 evicted


# ---------------------------------------------------------------------------
# Observers
# ---------------------------------------------------------------------------


class TestObservers:
    def test_null_observer_does_nothing(self):
        obs = NullObserver()
        # Should not raise and should not require a journal.
        obs.on_detected(_slack_signal())
        obs.on_work_item(_slack_signal(), 0.9)

    def test_journal_observer_records_lifecycle(self):
        j = SignalJournal()
        obs = JournalObserver(j)
        sig = _slack_signal(sid="s1")
        obs.on_detected(sig)
        obs.on_work_item(sig, confidence=0.88)
        entry = j.recent()[0]
        assert entry.outcome == "work_item"
        assert entry.confidence == 0.88
        assert entry.context["channel_id"] == "C1"

    def test_journal_observer_captures_gus_team_context(self):
        j = SignalJournal()
        obs = JournalObserver(j)
        sig = _gus_signal(sid="g1", team="Falcon")
        obs.on_detected(sig)
        obs.on_review(sig, reason="uncertain")
        entry = j.recent()[0]
        assert entry.context["team"] == "Falcon"
        assert entry.outcome == "review"
        assert entry.review_reason == "uncertain"

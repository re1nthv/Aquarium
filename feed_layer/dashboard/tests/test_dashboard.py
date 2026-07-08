"""Tests for the feed-layer operator dashboard (feed_layer.dashboard.app)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from feed_layer.control_plane import ControlStore, SignalJournal
from feed_layer.dashboard import create_dashboard_app


def _now():
    return datetime.now(tz=timezone.utc)


@pytest.fixture
def control_store():
    return ControlStore()  # no path -> in-memory only, nothing persisted


@pytest.fixture
def journal():
    j = SignalJournal()

    # Slack: one work item, one dropped.
    j.record_detected(
        signal_id="s1",
        source="slack",
        type="manual_reaction",
        originator_name="Alice",
        raw_content="please look at this outage",
        detected_at=_now() - timedelta(minutes=5),
        context={"channel_id": "C1"},
    )
    j.record_outcome("s1", "work_item", confidence=0.9)

    j.record_detected(
        signal_id="s2",
        source="slack",
        type="bot_message",
        originator_name="botty",
        raw_content="deploy succeeded",
        detected_at=_now() - timedelta(minutes=4),
        context={"channel_id": "C2"},
    )
    j.record_outcome("s2", "dropped", pre_filter_rule="noise")

    # GUS: one review.
    j.record_detected(
        signal_id="g1",
        source="gus",
        type="epic.created",
        originator_name="Bob",
        raw_content="Epic: ship the thing",
        detected_at=_now() - timedelta(minutes=3),
        context={"team": "Falcon"},
    )
    j.record_outcome("g1", "review", review_reason="uncertain")

    # Google Workspace: one work item.
    j.record_detected(
        signal_id="w1",
        source="google_workspace",
        type="doc.ready",
        originator_name="Carol",
        raw_content="Spec doc ready for review",
        detected_at=_now() - timedelta(minutes=2),
        context={"file_name": "spec.doc"},
    )
    j.record_outcome("w1", "work_item", confidence=0.75)

    return j


@pytest.fixture
def app(control_store, journal):
    return create_dashboard_app(control_store, journal)


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Page + health
# ---------------------------------------------------------------------------


def test_index_returns_html_with_title(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.content_type
    body = res.get_data(as_text=True)
    assert "Feed Layer" in body


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


def test_status_returns_three_modules_with_correct_counts(client):
    res = client.get("/api/status")
    assert res.status_code == 200
    data = res.get_json()
    modules = {m["key"]: m for m in data["modules"]}

    assert set(modules.keys()) == {"slack", "gus", "google_workspace"}

    assert modules["slack"]["detected"] == 2
    assert modules["slack"]["work_items"] == 1
    assert modules["slack"]["last_detected"] is not None

    assert modules["gus"]["detected"] == 1
    assert modules["gus"]["work_items"] == 0

    assert modules["google_workspace"]["detected"] == 1
    assert modules["google_workspace"]["work_items"] == 1

    # Fail-open defaults: no config narrowed yet -> enabled/following, scope "all ...".
    assert modules["slack"]["enabled"] is True
    assert modules["slack"]["scope"] == "all channels"
    assert modules["gus"]["enabled"] is True
    assert modules["gus"]["scope"] == "all teams"


def test_status_reflects_narrowed_slack_scope(client, control_store):
    control_store.add_slack_channel("C1", name="general", enabled=True)
    control_store.add_slack_channel("C2", name="random", enabled=False)
    res = client.get("/api/status")
    modules = {m["key"]: m for m in res.get_json()["modules"]}
    assert modules["slack"]["scope"] == "2 channels"
    # At least one channel enabled -> module considered enabled.
    assert modules["slack"]["enabled"] is True


# ---------------------------------------------------------------------------
# /api/signals
# ---------------------------------------------------------------------------


def test_signals_returns_all_entries(client):
    res = client.get("/api/signals")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["signals"]) == 4


def test_signals_filter_by_source(client):
    res = client.get("/api/signals?source=slack")
    data = res.get_json()
    assert len(data["signals"]) == 2
    assert all(s["source"] == "slack" for s in data["signals"])


def test_signals_filter_by_outcome(client):
    res = client.get("/api/signals?outcome=work_item")
    data = res.get_json()
    assert len(data["signals"]) == 2
    assert all(s["outcome"] == "work_item" for s in data["signals"])


def test_signals_filter_by_source_and_outcome(client):
    res = client.get("/api/signals?source=slack&outcome=dropped")
    data = res.get_json()
    assert len(data["signals"]) == 1
    assert data["signals"][0]["signal_id"] == "s2"


def test_signals_limit_defaults_and_caps(client, journal):
    for i in range(10):
        journal.record_detected(
            signal_id=f"extra-{i}",
            source="slack",
            type="manual_reaction",
            raw_content="x",
            detected_at=_now(),
        )
    res = client.get("/api/signals?limit=3")
    assert len(res.get_json()["signals"]) == 3

    res = client.get("/api/signals?limit=999999")
    # Capped at 500, but we don't have that many entries -> just assert no error
    # and that it doesn't exceed the cap.
    assert res.status_code == 200
    assert len(res.get_json()["signals"]) <= 500


# ---------------------------------------------------------------------------
# /api/work-items
# ---------------------------------------------------------------------------


def test_work_items_returns_only_work_items(client):
    res = client.get("/api/work-items")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["work_items"]) == 2
    assert all(w["outcome"] == "work_item" for w in data["work_items"])
    ids = {w["signal_id"] for w in data["work_items"]}
    assert ids == {"s1", "w1"}


# ---------------------------------------------------------------------------
# Slack channel config
# ---------------------------------------------------------------------------


def test_slack_channel_crud_flow(client, control_store):
    # Add
    res = client.post("/api/config/slack/channels", json={"id": "C9", "name": "eng"})
    assert res.status_code == 201
    added = res.get_json()
    assert added["id"] == "C9"
    assert added["name"] == "eng"
    assert added["enabled"] is True

    # List shows it
    res = client.get("/api/config/slack/channels")
    channels = res.get_json()["channels"]
    assert any(c["id"] == "C9" for c in channels)

    # Toggle disables it
    res = client.put("/api/config/slack/channels/C9", json={"enabled": False})
    assert res.status_code == 200
    assert res.get_json()["enabled"] is False
    assert control_store.slack_channel_enabled("C9") is False

    # Remove
    res = client.delete("/api/config/slack/channels/C9")
    assert res.status_code == 200
    assert res.get_json() == {"ok": True}
    res = client.get("/api/config/slack/channels")
    assert not any(c["id"] == "C9" for c in res.get_json()["channels"])


def test_slack_channel_post_missing_id_is_400(client):
    res = client.post("/api/config/slack/channels", json={"name": "no id here"})
    assert res.status_code == 400


def test_slack_channel_put_missing_enabled_is_400(client, control_store):
    control_store.add_slack_channel("C5")
    res = client.put("/api/config/slack/channels/C5", json={})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# GUS team config
# ---------------------------------------------------------------------------


def test_gus_team_crud_flow(client, control_store):
    res = client.post("/api/config/gus/teams", json={"team": "Falcon"})
    assert res.status_code == 201
    assert "Falcon" in res.get_json()["teams"]

    res = client.get("/api/config/gus/teams")
    assert "Falcon" in res.get_json()["teams"]
    assert control_store.gus_team_followed("Falcon") is True
    # Fail-open no longer applies once a team is configured.
    assert control_store.gus_team_followed("OtherTeam") is False

    res = client.delete("/api/config/gus/teams/Falcon")
    assert res.status_code == 200
    assert "Falcon" not in res.get_json()["teams"]
    assert control_store.gus_team_followed("Falcon") is True  # back to fail-open


def test_gus_team_post_missing_team_is_400(client):
    res = client.post("/api/config/gus/teams", json={})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Bad bodies generally
# ---------------------------------------------------------------------------


def test_post_slack_channel_bad_body_is_400(client):
    res = client.post(
        "/api/config/slack/channels",
        data="not json",
        content_type="application/json",
    )
    assert res.status_code == 400


def test_post_gus_team_no_body_is_400(client):
    res = client.post("/api/config/gus/teams")
    assert res.status_code == 400

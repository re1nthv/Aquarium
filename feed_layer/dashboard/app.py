"""Flask app factory for the feed-layer operator dashboard.

The dashboard is read+config only: it never runs a connector itself. It is
constructed with references to the *same* ``ControlStore``/``SignalJournal``
instances the connectors and the classifier are wired to, so every read
reflects live process state and every config write (Slack channel toggle, GUS
team follow/unfollow) takes effect immediately for the running pipeline.

Usage
-----
    from feed_layer.dashboard import create_dashboard_app
    from feed_layer.control_plane import ControlStore, SignalJournal

    control_store = ControlStore(path="control.json")
    journal = SignalJournal()
    app = create_dashboard_app(control_store, journal)
    app.run(port=4000)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from flask import Flask, jsonify, render_template, request

from feed_layer.control_plane import ControlStore, SignalJournal

# Default module cards shown on the status row. A caller may override this via
# create_dashboard_app(..., module_registry=[...]) to add/rename modules;
# each entry needs at least "key" (matches Signal.source) and "label".
DEFAULT_MODULE_REGISTRY: list[dict[str, str]] = [
    {"key": "slack", "label": "Slack"},
    {"key": "gus", "label": "GUS"},
    {"key": "google_workspace", "label": "Google Workspace"},
]

# Hard ceiling on ?limit= for the feed/work-item endpoints so a careless
# operator request can't force a huge JSON payload.
_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100

# Used internally to scan the *entire* journal for per-source work-item
# counts on the status cards (independent of any ?limit= a client passed).
# SignalJournal is a bounded ring (default max_entries=2000), so this is
# always an over-estimate rather than an unbounded scan.
_STATUS_SCAN_LIMIT = 1_000_000


def create_dashboard_app(
    control_store: ControlStore,
    journal: SignalJournal,
    module_registry: Optional[list[dict[str, str]]] = None,
) -> Flask:
    """Create and return the configured Flask dashboard application.

    Parameters
    ----------
    control_store:
        The live ``ControlStore`` shared with the connectors — reads populate
        the config panel, writes take effect immediately.
    journal:
        The live ``SignalJournal`` shared with the classifier's
        ``JournalObserver`` — reads populate the status cards and feeds.
    module_registry:
        Optional override of the status-card modules. Defaults to
        Slack / GUS / Google Workspace. Each entry is a dict with "key"
        (matching a Signal.source value) and "label" (display name).
    """
    modules = module_registry if module_registry is not None else DEFAULT_MODULE_REGISTRY

    app = Flask(__name__)

    # -----------------------------------------------------------------------
    # Route: liveness probe
    # -----------------------------------------------------------------------
    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    # -----------------------------------------------------------------------
    # Route: the dashboard page itself
    # -----------------------------------------------------------------------
    @app.get("/")
    def index():
        return render_template("index.html")

    # -----------------------------------------------------------------------
    # Route: per-module status cards
    # -----------------------------------------------------------------------
    @app.get("/api/status")
    def api_status():
        counts = journal.counts()
        by_source: dict[str, int] = counts.get("by_source", {})
        last_detected: dict[str, str] = counts.get("last_detected", {})

        work_by_source: dict[str, int] = {}
        for entry in journal.work_items(limit=_STATUS_SCAN_LIMIT):
            work_by_source[entry.source] = work_by_source.get(entry.source, 0) + 1

        return jsonify(
            {
                "modules": [
                    _module_status(
                        key=m["key"],
                        label=m.get("label", m["key"]),
                        control_store=control_store,
                        detected=by_source.get(m["key"], 0),
                        work_items=work_by_source.get(m["key"], 0),
                        last_detected=last_detected.get(m["key"]),
                    )
                    for m in modules
                ]
            }
        )

    # -----------------------------------------------------------------------
    # Route: detected-signal feed
    # -----------------------------------------------------------------------
    @app.get("/api/signals")
    def api_signals():
        source = request.args.get("source") or None
        outcome = request.args.get("outcome") or None
        limit = _parse_limit(request.args.get("limit"))
        entries = journal.recent(limit, source=source, outcome=outcome)
        return jsonify({"signals": [e.to_dict() for e in entries]})

    # -----------------------------------------------------------------------
    # Route: work-item feed
    # -----------------------------------------------------------------------
    @app.get("/api/work-items")
    def api_work_items():
        limit = _parse_limit(request.args.get("limit"))
        entries = journal.work_items(limit)
        return jsonify({"work_items": [e.to_dict() for e in entries]})

    # -----------------------------------------------------------------------
    # Routes: Slack channel config
    # -----------------------------------------------------------------------
    @app.get("/api/config/slack/channels")
    def get_slack_channels():
        return jsonify(
            {"channels": [asdict(c) for c in control_store.slack_channels()]}
        )

    @app.post("/api/config/slack/channels")
    def post_slack_channel():
        body = _json_body()
        if body is None:
            return jsonify({"error": "expected a JSON object body"}), 400
        channel_id = body.get("id")
        if not channel_id or not isinstance(channel_id, str):
            return jsonify({"error": "id is required"}), 400
        name = body.get("name") or ""
        if not isinstance(name, str):
            return jsonify({"error": "name must be a string"}), 400
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            return jsonify({"error": "enabled must be a boolean"}), 400
        channel = control_store.add_slack_channel(channel_id, name=name, enabled=enabled)
        return jsonify(asdict(channel)), 201

    @app.put("/api/config/slack/channels/<channel_id>")
    def put_slack_channel(channel_id: str):
        body = _json_body()
        if body is None or "enabled" not in body or not isinstance(body["enabled"], bool):
            return jsonify({"error": "enabled (boolean) is required"}), 400
        channel = control_store.set_slack_channel_enabled(channel_id, body["enabled"])
        return jsonify(asdict(channel))

    @app.delete("/api/config/slack/channels/<channel_id>")
    def delete_slack_channel(channel_id: str):
        control_store.remove_slack_channel(channel_id)
        return jsonify({"ok": True})

    # -----------------------------------------------------------------------
    # Routes: GUS team config
    # -----------------------------------------------------------------------
    @app.get("/api/config/gus/teams")
    def get_gus_teams():
        return jsonify({"teams": control_store.gus_teams()})

    @app.post("/api/config/gus/teams")
    def post_gus_team():
        body = _json_body()
        team = body.get("team") if body is not None else None
        if not team or not isinstance(team, str) or not team.strip():
            return jsonify({"error": "team is required"}), 400
        control_store.add_gus_team(team)
        return jsonify({"teams": control_store.gus_teams()}), 201

    @app.delete("/api/config/gus/teams/<team>")
    def delete_gus_team(team: str):
        control_store.remove_gus_team(team)
        return jsonify({"teams": control_store.gus_teams()})

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_body() -> Optional[dict[str, Any]]:
    """Best-effort JSON body parse; returns None on missing/malformed body."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None
    return body


def _parse_limit(raw: Optional[str]) -> int:
    """Parse a ?limit= query param, defaulting/clamping to a sane range."""
    if raw is None:
        return _DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    if value <= 0:
        return _DEFAULT_LIMIT
    return min(value, _MAX_LIMIT)


def _module_status(
    *,
    key: str,
    label: str,
    control_store: ControlStore,
    detected: int,
    work_items: int,
    last_detected: Optional[str],
) -> dict[str, Any]:
    """Derive a status card for one module from control_store + journal counts."""
    if key == "slack":
        channels = control_store.slack_channels()
        if not channels:
            enabled = True
            scope = "all channels"
        else:
            enabled = any(c.enabled for c in channels)
            scope = _pluralize(len(channels), "channel")
    elif key == "gus":
        teams = control_store.gus_teams()
        # GUS following is always fail-open active; team list only narrows scope.
        enabled = True
        scope = "all teams" if not teams else _pluralize(len(teams), "team")
    elif key == "google_workspace":
        gws = control_store.gws_config()
        enabled = True
        scope = "all folders" if not gws.get("folder_id") else "1 folder"
    else:
        enabled = True
        scope = "n/a"

    return {
        "key": key,
        "label": label,
        "enabled": enabled,
        "detected": detected,
        "work_items": work_items,
        "last_detected": last_detected,
        "scope": scope,
    }


def _pluralize(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"

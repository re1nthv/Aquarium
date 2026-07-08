"""Feed-layer operator dashboard.

A read+config Flask app: it renders a live view of the ``SignalJournal``
(detected signals, work items, rollup counts) and lets an operator edit the
``ControlStore`` (Slack channel toggles, GUS team following). It does not run
any connector itself — the caller constructs it with references to the same
``ControlStore``/``SignalJournal`` instances the connectors and classifier use.
"""

from .app import create_dashboard_app

__all__ = ["create_dashboard_app"]

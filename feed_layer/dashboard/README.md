# Feed Layer Dashboard

An operator console for the feed layer: track the signals being detected, see
which ones became work items, and control what each connector ingests.

The dashboard owns no state of its own. It is constructed with references to
the **shared** `ControlStore` and `SignalJournal` from
[`feed_layer/control_plane/`](../control_plane/) — the same objects the running
connectors and classifier write to — so what you see is live.

```
                    ┌──────────────────────────┐
   Slack connector ─┤                          │
   GUS connector   ─┤  ControlStore  (config)  │◄── dashboard edits (toggles)
   GWS connector   ─┤                          │
                    │  SignalJournal (record)  │──► dashboard reads (feed)
   Classifier ─────►┤     via SignalObserver   │
                    └──────────────────────────┘
```

## Running

```python
from feed_layer.control_plane import ControlStore, SignalJournal
from feed_layer.dashboard import create_dashboard_app

control = ControlStore(path="control.json")   # shared with the connectors
journal = SignalJournal()                       # shared with the classifier observer

app = create_dashboard_app(control, journal)
app.run(port=8080)
```

Wire the same `control` into `slack.create_app(..., control_store=control)` and
`gus.create_app(queue, control_store=control)`, and pass
`JournalObserver(journal)` to the `SignalClassifier`. Then toggling a channel or
following a team in the UI immediately changes what gets ingested, and every
classification outcome shows up in the feed.

## What it shows

- **Status cards** — per source: enabled state, signals detected, work items,
  last-detected time, and current scope (e.g. "2 channels", "all teams").
- **Detected Signals** — newest-first feed, filterable by source and outcome
  (`work_item`, `dropped`, `duplicate`, `review`, `parked`).
- **Work Items** — the signals classified relevant and forwarded downstream.
- **Config** — Slack channel toggles (add / enable / disable / remove) and GUS
  team following (add / remove).

## Design notes

- Single self-contained HTML page, vanilla JS + `fetch`, polling every ~4s. No
  npm or build step — consistent with the rest of the repo.
- **Fail-open** filtering: with nothing configured, every channel/team is
  allowed. Scope only narrows once the operator adds entries.
- Deferred for now: authentication, websocket push, editing the Google
  Workspace intake folder from the UI, and dashboards for the other layers.

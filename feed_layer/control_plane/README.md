# Feed Layer Control Plane

Shared runtime state that ties the feed layer's connectors, classifier, and
dashboard together. Two stores plus one hook — no business logic of its own.

## `ControlStore` — editable configuration

What the operator controls from the dashboard:

- **Slack channels** to sniff — a set of `{id, name, enabled}` entries.
- **GUS teams** to follow — item ingestion is scoped to these teams.

Properties:

- **Fail-open.** With nothing configured, `slack_channel_enabled(...)` and
  `gus_team_followed(...)` both return `True`. Wiring the store into a connector
  therefore changes nothing until an operator narrows scope — which is why the
  connectors' existing test suites stay green.
- **Persisted** to a JSON file (atomic write via temp-file + `os.replace`) when
  a `path` is given; a missing or malformed file yields empty defaults.
- **Thread-safe** — a webhook handler, a poller, and the dashboard can touch it
  concurrently.

## `SignalJournal` — observability record

A bounded, thread-safe log of every signal the pipeline sees, from detection to
its terminal outcome:

| Outcome | Meaning |
|---|---|
| `detected` | seen, not yet routed (transient) |
| `work_item` | classified relevant → forwarded downstream |
| `dropped` | classified irrelevant, or dropped by a pre-filter rule |
| `duplicate` | collapsed into an earlier signal by semantic dedup |
| `review` | classified uncertain → human review queue |
| `parked` | deferred (budget exhausted / LLM unavailable / stale), retried |

Query methods (`recent`, `work_items`, `counts`) back the dashboard's feed,
work-item list, and status cards. A ring buffer (`max_entries`) keeps memory
flat in a long-running process.

## `SignalObserver` — the classifier hook

The classifier stays decoupled from the dashboard by emitting lifecycle events
to an injected observer:

- `NullObserver` (default) — does nothing; classifier behaviour is unchanged.
- `JournalObserver(journal)` — forwards each event into a `SignalJournal`.

Pass `JournalObserver(journal)` to `SignalClassifier(..., observer=...)` to make
classification outcomes visible in the dashboard.

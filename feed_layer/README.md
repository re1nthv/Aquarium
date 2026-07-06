# Feed Layer

The entry point of the entire system. Responsible for ingesting raw signals from all external channels, normalising them into a canonical Signal schema, classifying them for relevance, deduplicating across sources, and forwarding only meaningful signals downstream to the Task Management Layer.

The core concern of this layer is signal quality over signal volume — consuming everything indiscriminately would be computationally prohibitive. This layer acts as the first gate.

---

## Canonical Signal Schema (v1.0)

All connectors produce a Signal object conforming to this schema. The schema is versioned — `schema_version` must be bumped when any field is added, removed, or changed in a breaking way.

```
Signal {
  id:             uuid                      // generated at ingest time
  schema_version: "1.0"
  source:         "slack" | "gus" | "google_workspace"
  type:           string                    // source-specific event type (see each connector)
  raw_content:    string                    // normalised plain text content
  timestamp:      ISO8601                   // time of the original event
  ingested_at:    ISO8601                   // time the connector received the event
  originator:     { id, name }             // who created or triggered the event
  metadata:       { ... }                  // source-specific fields (fully specified per connector)
  classification: { ... }                  // added by Signal Classifier after processing
}
```

---

## Sub-modules

### [slack/](slack/)
**Trigger:** Slack Events API — `reaction_added` (manual emoji gate) and `message` with `is_bot: true` (automated bot path). Subscribes to `reaction_removed` to cancel signals if the emoji is withdrawn. Responds HTTP 200 immediately and enqueues asynchronously.

Signal types produced: `manual_reaction`, `bot_message`

Key constraints:
- Bot detection uses Slack's `is_bot` API flag, not a static allowlist.
- The emoji gate is a toggle (handles `reaction_removed`), not a latch.
- NLP-based informal handshake detection is deferred to a future iteration.

---

### [gus/](gus/)
**Trigger:** GUS webhooks (primary) for `epic.created`, `epic.updated`, `td.created`, `td.updated`, `escalation.created`, and `task.status_changed` where status = `Blocked`. Polling fallback every 5 minutes using a durable watermark cursor. Manual `aquarium-intake` label as an opt-in gate for any item type.

Signal types produced: `epic.created`, `epic.updated`, `td.created`, `td.updated`, `escalation.created`, `task.blocked`, `manual_label`

Key constraints:
- Task-level CRUD is excluded by default; only `task.blocked` is subscribed.
- Polling cursor is persisted durably — crash recovery resumes from last committed position.
- Rapid updates to the same item within 60 seconds are collapsed to the latest version.

---

### [google_workspace/](google_workspace/)
**Trigger:** Google Drive API watch channel on a designated intake folder. A document is ingested only when it is in the intake folder AND its title ends with ` [READY]` (both conditions required). Watch channel is renewed every 6 days before the 7-day Google expiry. Polling fallback every 15 minutes.

Signal types produced: `document.ready`

Key constraints:
- Connector runs as a read-only service account scoped to the intake folder only — no org-wide Drive access.
- The `[READY]` title suffix is the resolved "ready" convention — visible, simple, no extra API scope required.
- 403 on document fetch (permission denied) is logged and not retried — sharing settings must be corrected by a human.

---

### [signal_classifier/](signal_classifier/)
**Trigger:** Queue consumer — pulls from the internal message queue that all three connectors write to. Pull-based with configurable batch size (default 10). Multiple worker instances can run concurrently.

Processing pipeline per signal:
1. **Rule-based pre-filter** — drops obvious noise cheaply before the LLM is invoked.
2. **Cross-source semantic deduplication** — embedding similarity check (cosine > 0.92) within a 2-hour window to detect the same real-world event arriving from multiple sources.
3. **LLM relevance classification** — returns `relevant | irrelevant | uncertain`. Prompt is versioned. Cost cap enforced per day — budget-exhausted signals queue, never drop.
4. **Forward or park** — relevant signals go downstream; uncertain signals go to the human review queue with a 4-hour SLA and a feedback loop that accumulates labelled examples to improve the prompt over time.

Key constraints:
- LLM is never called synchronously in a webhook handler.
- LLM unavailability parks signals as `uncertain` with reason `llm_unavailable`; they are retried, not dropped.
- 14 observability metrics emitted per processing cycle (see connector README for full list).

---

## Data Flow

```
Slack ──── reaction_added / bot_message ──────────────────┐
                                                           │
GUS ─────── webhook / poll / manual_label ────────────────┤──► Internal Message Queue
                                                           │         │
Google WS ── Drive watch / poll ─────────────────────────┘         │
                                                                     ▼
                                                          Signal Classifier
                                                          ┌───────────────────┐
                                                          │ 1. Pre-filter      │
                                                          │ 2. Semantic dedup  │
                                                          │ 3. LLM classify    │
                                                          │ 4. Forward / park  │
                                                          └───────────────────┘
                                                                     │
                                              ┌──────────────────────┼────────────────┐
                                              ▼                      ▼                ▼
                                         relevant              irrelevant          uncertain
                                              │                      │                │
                                     Task Management            dropped          Human Review
                                         Layer                                    Queue (4h SLA)
```

---

## Cross-cutting Concerns

### Delivery guarantee
All connectors return HTTP 200 immediately and enqueue asynchronously. The internal message queue provides at-least-once delivery. The classifier is idempotent on signal `id` to handle redelivery.

### Authentication
- Slack: bot token with `reactions:read`, `channels:history`, `channels:read` OAuth scopes.
- GUS: API key or OAuth, scoped to read-only on the team's epics, TDs, and escalations.
- Google Workspace: service account with `drive.readonly` on the intake folder.

### PII handling
Raw signal content is not written to application logs. Only signal `id`, `source`, `type`, and classification metadata are logged. Raw content is stored only in the signal store with access-controlled read permissions.

# GUS Feed

Responsible for ingesting signals from GUS work items and normalising them into the canonical Signal schema.

---

## Trigger Mechanism

Two trigger paths: webhook-driven (primary) and poll-driven (fallback). Task-level CRUD is not ingested by default — the connector subscribes at epic and TD level. Task-level events can be opted in via manual labels.

### Path 1 — Webhook (primary)
- **Trigger condition:** GUS fires a webhook on the following event types:
  - `epic.created` — a new epic is created and assigned to the team's release
  - `epic.updated` — fields on an existing epic change (title, description, assignee, release target)
  - `td.created` — a team dependency is raised on this team by another team
  - `td.updated` — a TD changes status (e.g. blocked → unblocked)
  - `escalation.created` — a customer escalation is filed and routed to the team
- **What is NOT subscribed by default:** `task.created`, `task.updated`, `task.deleted` — task-level CRUD is too noisy. However, one task-level event IS subscribed: `task.status_changed` where new status = `Blocked`, because a blocked task is an actionable signal.
- **Endpoint:** A registered webhook endpoint that returns HTTP 200 immediately and enqueues the event.

### Path 2 — Polling fallback
Used when GUS webhooks are unavailable or unreliable.

- **Polling interval:** Every 5 minutes.
- **Watermark:** The connector stores a cursor — the `updated_at` timestamp of the last successfully processed item. On each poll, it fetches all items modified after the cursor.
- **Crash recovery:** The cursor is persisted to durable storage (not in-memory). On restart, polling resumes from the last committed cursor, not from the current time.
- **Overlap guard:** If a poll run takes longer than 5 minutes, the next run is skipped (not overlapped) to avoid duplicate ingestion.
- **Rate limiting:** GUS API calls are made with a maximum of 10 requests per second, with exponential backoff on 429 responses.

### Path 3 — Manual label gate (secondary, any event type)
- **Trigger condition:** A team member applies the label `aquarium-intake` to any GUS work item — including task-level items that would otherwise be excluded.
- **Purpose:** Allows teams to force any item into the pipeline regardless of event type, and provides a human gate for items the automated path misses.
- **How detected:** Webhook subscription on `label.added` events, filtered for the `aquarium-intake` label. Polling fallback scans for the label on each cycle.

---

## Signal Schema Output

```
Signal {
  id:             uuid
  schema_version: "1.0"
  source:         "gus"
  type:           "epic.created" | "epic.updated" | "td.created" | "td.updated"
                  | "escalation.created" | "task.blocked" | "manual_label"
  raw_content:    string                    // title + description of the work item
  timestamp:      ISO8601                   // GUS item created_at or updated_at
  ingested_at:    ISO8601
  originator:     { gus_user_id, name }
  metadata: {
    gus_item_id:    string                  // unique GUS work item ID — primary dedup key
    gus_item_type:  "epic" | "task" | "td" | "escalation"
    gus_item_url:   string
    release:        string | null           // release label if present
    team:           string                  // owning team
    label_applied:  string | null           // for manual_label type only
  }
}
```

---

## Delivery Guarantee

- Webhook handler returns HTTP 200 immediately and enqueues the payload. Classification is asynchronous.
- Polling writes a durable cursor after each successfully enqueued batch, not after classification.
- Both paths write to the same internal message queue, ensuring a single consumption path downstream.

---

## Deduplication Anchor

- `metadata.gus_item_id` is the stable unique identifier for a GUS work item. Use this as the dedup key within the GUS source.
- On `epic.updated`, if the same epic fires multiple rapid updates, only the latest version of the item (by `updated_at`) is forwarded — earlier versions within a 60-second window are collapsed.
- Cross-source deduplication (e.g. same incident in both GUS and Slack) is handled by the Signal Classifier.

---

## What is NOT handled here

- Task-level CRUD (create/update/delete) unless manually labelled or status = Blocked.
- Relevance classification — that is the Signal Classifier's job.
- Cross-source deduplication — handled at the classifier layer.

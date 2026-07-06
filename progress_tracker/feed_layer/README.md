# Progress — Feed Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| slack | 🟡 In Progress | Trigger mechanism + schema defined; not yet implemented |
| gus | 🟡 In Progress | Trigger mechanism + schema defined; not yet implemented |
| google_workspace | 🟡 In Progress | Trigger mechanism + schema + ready convention defined; not yet implemented |
| signal_classifier | 🟡 In Progress | Full processing pipeline defined (pre-filter → dedup → LLM → forward); not yet implemented |

**Layer status: 🟡 In Progress**

---

## Sub-module Detail

### slack — 🟡 In Progress
**Design complete. Implementation not started.**

**What is defined:**
- Trigger: `reaction_added` (emoji gate, toggle via `reaction_removed`) + `message` with `is_bot: true`
- Signal schema v1.0 with all fields specified
- Delivery guarantee: async enqueue, HTTP 200 on receipt
- Dedup anchor: `message_ts`
- NLP handshake path explicitly deferred

**What needs to be built:**
- Slack app registration and OAuth scopes (`reactions:read`, `channels:history`, `channels:read`)
- Webhook endpoint (returns 200, enqueues to message queue)
- `reaction_removed` handler for signal withdrawal
- Signal normalisation into canonical schema
- Bot message listener with `is_bot` flag check
- Message queue integration

---

### gus — 🟡 In Progress
**Design complete. Implementation not started.**

**What is defined:**
- Trigger: webhooks for `epic.*`, `td.*`, `escalation.created`, `task.blocked`; polling fallback with durable cursor; manual `aquarium-intake` label path
- Signal schema v1.0 with all fields specified
- Polling semantics: 5-minute interval, watermark cursor, crash recovery, overlap guard, rate limit
- Rapid-update collapse: 60-second window per item

**What needs to be built:**
- GUS webhook registration for subscribed event types
- Webhook endpoint (returns 200, enqueues)
- Polling job with durable cursor store
- `aquarium-intake` label listener
- Signal normalisation into canonical schema
- Message queue integration

---

### google_workspace — 🟡 In Progress
**Design complete. Implementation not started.**

**What is defined:**
- Trigger: Drive API watch on intake folder; `[READY]` title suffix as the ready convention
- Signal schema v1.0 with all fields specified
- Watch channel renewal: scheduled job every 6 days
- Polling fallback: 15-minute interval
- Access model: read-only service account scoped to intake folder
- 403 handling: log and do not retry

**What needs to be built:**
- Google service account creation and Drive folder scoping
- Drive watch channel setup and renewal job
- `[READY]` suffix filter logic
- Document body fetch (Docs API / export)
- Polling fallback job with durable cursor
- Signal normalisation into canonical schema
- Message queue integration

---

### signal_classifier — 🟡 In Progress
**Design complete. Implementation not started.**

**What is defined:**
- Trigger: queue consumer (pull-based, batch 10, multiple workers)
- Step 1: rule-based pre-filter (empty content, known-spam bots, stale signals)
- Step 2: cross-source semantic dedup (embedding similarity, cosine > 0.92, 2-hour window)
- Step 3: LLM classification (`relevant | irrelevant | uncertain`), versioned prompt, daily token budget, fallback on LLM unavailability
- Step 4: forward/drop/park logic
- Human review queue: 4-hour SLA, escalation path, feedback loop → 50-example prompt review trigger
- 14 observability metrics defined

**What needs to be built:**
- Message queue consumer workers
- Rule-based pre-filter implementation
- Embedding model integration for semantic dedup
- Dedup store (vector store or similarity index with 2-hour TTL)
- LLM integration with prompt v1, versioning, and token budget tracking
- Human review queue store and moderator notification (Slack DM)
- Feedback loop: labelled example store + 50-example accumulation trigger
- Downstream enqueue to Task Management Layer
- All 14 observability metrics instrumented

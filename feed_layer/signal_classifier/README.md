# Signal Classifier

The classification and deduplication gate. Consumes all normalised signals from the internal message queue, determines relevance, deduplicates across sources, and forwards clean signals downstream to the Task Management Layer.

---

## Trigger Mechanism

The classifier is a **queue consumer**, not a webhook listener. It is triggered by messages arriving on the internal message queue (e.g. SQS / Pub/Sub / Redis Stream) that all three connectors write to.

- **Consumption model:** Pull-based, with a configurable batch size (default: 10 signals per batch).
- **Concurrency:** Multiple classifier worker instances can run in parallel, each pulling from the queue. The queue ensures each signal is processed by exactly one worker (at-least-once delivery + idempotent processing via signal `id`).
- **Backpressure:** If the downstream Task Management Layer is slow, workers slow their pull rate. Signals accumulate in the queue rather than being dropped.

---

## Processing Pipeline (per signal)

Each signal goes through four sequential steps:

### Step 1 — Rule-based pre-filter (cheap, runs first)
Before the LLM is called, a fast rule-based filter drops obvious noise:

- Signal `raw_content` is empty or under 10 characters → **drop**
- Signal `source = slack`, `type = bot_message`, bot name is in the known-spam list (e.g. `giphy`, `polly`) → **drop**
- Signal `source = gus`, item type = `task`, status change is not `Blocked` → **drop** (should not reach here but defensive guard)
- Signal `ingested_at` is more than 48 hours old (stale replay) → **park** in the uncertain queue with reason `stale`

Any signal that passes the pre-filter proceeds to Step 2.

### Step 2 — Cross-source semantic deduplication
Before classifying, check if this signal describes the same real-world event as a recently processed signal from a different source.

- **How:** Compute a short embedding of `raw_content` (using a lightweight embedding model, not the full LLM). Compare against embeddings of signals processed in the last 2 hours using cosine similarity.
- **Threshold:** Cosine similarity > 0.92 → considered a duplicate.
- **On duplicate detected:** The later-arriving signal is marked as a duplicate and linked to the canonical signal ID. It is not forwarded downstream. Both signals are retained in the store for audit.
- **Dedup window:** 2 hours. Events farther apart than 2 hours are treated as independent even if semantically similar (a recurring bug is not the same task each time).
- **Within-source dedup:** Not handled here — each connector handles its own dedup anchor (Slack: `message_ts`, GUS: `gus_item_id`, Drive: `file_id`).

### Step 3 — LLM relevance classification
Signals that pass dedup are classified by an LLM.

- **Input to LLM:** The normalised signal fields — source, type, raw_content, originator, and key metadata fields. Raw content is truncated to 2,000 tokens before the LLM call.
- **Output:** One of three labels:
  - `relevant` — signal represents actionable work; forward downstream
  - `irrelevant` — noise, off-topic, or already handled; drop
  - `uncertain` — the LLM cannot confidently classify; route to human review queue
- **Pre-filter before LLM:** The rule-based filter in Step 1 is the cost control. The LLM only sees signals that passed the cheap filter.
- **Cost cap:** A per-day token budget is configured at deployment time. If the budget is exhausted, new signals are held in the queue and processed the next day — they are never dropped.
- **Prompt versioning:** The classification prompt is versioned (e.g. `v1`, `v2`). The version used is stored alongside the signal's classification result. Changing the prompt does not retroactively reclassify past signals.
- **Fallback on LLM unavailability:** If the LLM provider returns 5xx or rate-limit errors after 3 retries with exponential backoff, the signal is placed in the `uncertain` queue with reason `llm_unavailable`. It is retried when the LLM recovers, not dropped.

### Step 4 — Forward or park
- `relevant` → enqueue to the Task Management Layer's inbound queue with the full Signal payload + classification metadata
- `irrelevant` → mark as dropped in the signal store; no downstream action
- `uncertain` → write to the human review queue (see below)

---

## Human Review Queue

For signals the classifier cannot confidently resolve.

- **Owner:** The team's designated pipeline moderator (configured per deployment). Moderator is notified via Slack DM when a new uncertain signal arrives.
- **SLA:** Uncertain signals must be reviewed within 4 hours. After 4 hours without a decision, the signal is automatically escalated to a senior moderator.
- **Reviewer actions:** `approve` (treat as relevant, forward downstream), `reject` (treat as irrelevant, drop), `defer` (extend by 4 hours).
- **Feedback loop:** Every moderator decision is stored as a labelled training example `(signal, decision)`. Once 50 new labelled examples accumulate, a prompt-tuning review is triggered to evaluate whether the classification prompt should be updated. This is the mechanism by which the classifier improves over time.

---

## Signal Schema — Classifier Additions

The classifier enriches the Signal object before forwarding:

```
Signal {
  ...all connector fields...
  classification: {
    result:           "relevant" | "irrelevant" | "uncertain"
    prompt_version:   "v1"
    confidence:       float | null          // if the LLM returns a confidence score
    duplicate_of:     uuid | null           // canonical signal ID if duplicate
    classified_at:    ISO8601
    pre_filter_hit:   string | null         // rule name if dropped by pre-filter
  }
}
```

---

## Observability

The following metrics are emitted per processing cycle:

| Metric | Description |
|---|---|
| `signals.ingested` | Total signals received from queue, by source |
| `signals.pre_filter_dropped` | Dropped by rule-based pre-filter, by rule name |
| `signals.deduplicated` | Marked as cross-source duplicates |
| `signals.classified.relevant` | Forwarded downstream |
| `signals.classified.irrelevant` | Dropped after LLM classification |
| `signals.classified.uncertain` | Routed to human review queue |
| `signals.uncertain_queue.depth` | Current size of the unreviewed human queue |
| `signals.uncertain_queue.age_p90` | 90th percentile age of items in the human queue |
| `classifier.llm_tokens_used` | Tokens consumed by LLM calls, for cost tracking |
| `classifier.llm_fallback_count` | Signals parked due to LLM unavailability |

---

## What is NOT handled here

- Per-source deduplication — each connector owns its own dedup anchor.
- Signal storage beyond classification metadata — the Task Management Layer owns the persistent task store.
- Training the classification model — the feedback loop surfaces labelled examples, but prompt revision is a manual human step triggered by the accumulation of 50+ examples.

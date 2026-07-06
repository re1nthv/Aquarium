# Slack Feed

Responsible for ingesting signals from Slack and normalising them into the canonical Signal schema.

---

## Trigger Mechanism

The connector subscribes to the **Slack Events API** via a registered Slack app. Two distinct trigger paths exist — manual and automated — to avoid a single point of human bottleneck.

### Path 1 — Manual gate (emoji reaction)
- **Event subscribed:** `reaction_added`
- **Trigger condition:** A team member reacts to any message with the designated intake emoji (e.g. `:aquarium:`).
- **Inverse:** Also subscribe to `reaction_removed` on the same emoji. If a user removes the reaction before the signal is forwarded downstream, the signal is withdrawn from the queue. The gate is a toggle, not a latch.
- **What gets ingested:** The message text, thread context (parent + all replies if in a thread), channel, author, and timestamp.
- **Covers:** Production breaks reported by humans, feature requests, informal handshakes identified by a team member.

### Path 2 — Automated gate (known bot messages)
- **Event subscribed:** `message` with filter `subtype: bot_message` OR author field `is_bot: true` (Slack API flag — not a static allowlist).
- **Trigger condition:** A message is posted by a Slack bot in a monitored channel.
- **Monitored channels:** Configured per deployment (e.g. `#alerts`, `#bug-reports`). Not all channels — only explicitly allowlisted ones.
- **What gets ingested:** Full message text, bot name, channel, timestamp.
- **Covers:** Periodic bug reports from monitoring bots, alert bots, CI/CD bots.

### Path 3 — NLP-detected handshakes (future)
- Informal agreements in threads where no one applied the emoji.
- **Not in scope for initial build.** Will require a periodic thread scanner with an LLM pass. Deferred until Path 1 and 2 are stable and producing signal volume to calibrate against.

---

## Signal Schema Output

Every signal produced by this connector conforms to the canonical `Signal` schema:

```
Signal {
  id:          uuid                      // generated at ingest time
  schema_version: "1.0"                  // bumped when schema changes
  source:      "slack"
  type:        "manual_reaction"         // or "bot_message"
  raw_content: string                    // message text (+ thread if applicable)
  timestamp:   ISO8601                   // time of original message
  ingested_at: ISO8601                   // time connector received the event
  originator:  { slack_user_id, name }
  metadata: {
    channel_id:    string
    thread_ts:     string | null         // parent thread timestamp if in a thread
    message_ts:    string                // Slack message timestamp (used as dedup anchor)
    reaction:      string | null         // emoji name, for Path 1 only
    bot_name:      string | null         // for Path 2 only
  }
}
```

---

## Delivery Guarantee

- Slack Events API retries delivery with exponential backoff if the endpoint does not return HTTP 200 within 3 seconds.
- The connector endpoint must respond 200 immediately upon receipt and enqueue the event into the internal message queue (e.g. SQS / Pub/Sub / Redis Stream). **Do not call the classifier synchronously in the webhook handler.**
- The classifier consumes from the queue asynchronously, decoupling ingest latency from classification latency.
- If the queue is unavailable, return HTTP 503 — Slack will retry.

---

## Deduplication Anchor

- `metadata.message_ts` is the Slack-assigned unique timestamp for the message. Use this as the dedup key within the Slack source.
- Cross-source deduplication (e.g. same incident in both Slack and GUS) is handled by the Signal Classifier, not here.

---

## What is NOT handled here

- Relevance classification — that is the Signal Classifier's job.
- Cross-source deduplication — handled at the classifier layer.
- NLP-based handshake detection — deferred (Path 3 above).

# Changelog

A running log of every change to the system — design, implementation, or structural. Most recent entry at the top.

Format: `[YYYY-MM-DD] [layer/sub-module] [type: design | impl | fix | refactor | struct] — description`

---

## Log

- [2026-07-08] [feed_layer/signal_classifier] [fix] — Hardening: populate `pre_filter_hit` on dropped/parked signals for audit; date-aware injectable token budget (`TokenBudgetStore` + `InMemoryTokenBudgetStore`, resets at day boundary, durable-backend swappable); explicit raw_content truncation before the LLM call (`max_llm_content_chars`, original signal unmutated). 36/36 tests passing (+9).
- [2026-07-08] [feed_layer/google_workspace] [fix] — Hardening: advance Drive `page_token` after each `list_changes` (backward-compatible dict/tuple/legacy-list return shapes) to stop duplicate processing on clustered notifications; catch watch-channel renewal failures (log, don't advance expiry, retry next cycle) instead of propagating. 33/33 tests passing (+8).
- [2026-07-08] [feed_layer/gus] [fix] — Hardening: polling now gates task items on `status == "Blocked"` and skips unknown item types (was mis-typing everything to task.blocked/epic.updated); added `RateLimiter` (min-interval, injectable clock/sleep) wired into GusPoller per `MAX_REQUESTS_PER_SECOND`; added durable `FileCursorStore` (atomic JSON write, epoch fallback on missing/malformed). 37/37 tests passing (+14).
- [2026-07-08] [feed_layer/slack] [fix] — Hardening: implemented Slack request signature verification (HMAC-SHA256 over `v0:{ts}:{body}`, 5-min replay window, 401 on failure, skipped when no signing secret configured); fixed multi-reaction race by matching removal on (message_ts, reactor_id) via new optional `SlackMetadata.reactor_id`. 24/24 tests passing (+7).

- [2026-07-06] [feed_layer/slack] [impl] — Implemented connector.py (Flask, reaction_added/removed toggle + bot_message via is_bot flag, async enqueue, 503 on queue failure). 17/17 tests passing.
- [2026-07-06] [feed_layer/gus] [impl] — Implemented connector.py (webhook handler + GusPoller with durable cursor, rapid-update collapse, crash-safe watermark). 23/23 tests passing.
- [2026-07-06] [feed_layer/google_workspace] [impl] — Implemented connector.py (Drive watch + polling fallback, [READY] gate, 5-min dedup, WatchChannelRenewer, 403 handling). 25/25 tests passing.
- [2026-07-06] [feed_layer/signal_classifier] [impl] — Implemented classifier.py (pre-filter → semantic dedup → LLM classify → route, retry backoff, token budget, human review queue, 10 metrics). 27/27 tests passing.
- [2026-07-06] [feed_layer/shared] [impl] — Canonical Signal schema v1.0, MessageQueue ABC, InMemoryQueue written as shared contract for all connectors.

- [2026-07-06] [feed_layer/signal_classifier] [design] — Full processing pipeline defined: rule-based pre-filter → semantic dedup (embedding cosine similarity, 2h window) → LLM classification (versioned prompt, daily token budget, fallback) → forward/park. Human review queue with 4h SLA and feedback loop specified. 14 observability metrics defined.
- [2026-07-06] [feed_layer/google_workspace] [design] — Trigger mechanism defined: Drive API watch + polling fallback. Ready convention resolved: intake folder + `[READY]` title suffix (both required). Service account auth, 403 handling, and watch renewal job specified.
- [2026-07-06] [feed_layer/gus] [design] — Trigger mechanism defined: webhooks for epic/TD/escalation/task.blocked, polling fallback with durable cursor (5min interval, crash recovery, overlap guard, rate limit), manual `aquarium-intake` label path. Signal schema v1.0 and 60s update collapse specified.
- [2026-07-06] [feed_layer/slack] [design] — Trigger mechanism defined: emoji reaction gate (toggle via reaction_removed) + bot_message path using is_bot flag. Signal schema v1.0, async delivery guarantee, dedup anchor specified. NLP handshake path explicitly deferred.
- [2026-07-06] [feed_layer] [design] — Canonical Signal schema v1.0 defined and documented at layer level. Cross-cutting concerns defined: delivery guarantee, authentication scopes, PII handling.
- [2026-07-06] [all] [struct] — Progress tracker module added; all sub-modules initialised at 🔴 Not Started
- [2026-07-06] [all] [design] — Responsibilities documented in README at every sub-module, layer, and root node
- [2026-07-06] [all] [struct] — Initial folder structure created for all four layers and their sub-modules

---

> When any sub-module moves status, add an entry here and update the relevant layer README and the root dashboard.

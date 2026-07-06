# Changelog

A running log of every change to the system — design, implementation, or structural. Most recent entry at the top.

Format: `[YYYY-MM-DD] [layer/sub-module] [type: design | impl | fix | refactor | struct] — description`

---

## Log

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

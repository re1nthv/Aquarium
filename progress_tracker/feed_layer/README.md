# Progress — Feed Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| slack | 🟢 Complete | Implemented + hardened + channel-sniff gate — 31 tests passing |
| gus | 🟢 Complete | Implemented + hardened + team-follow gate — 42 tests passing |
| google_workspace | 🟢 Complete | Implemented + hardened — 33 tests passing |
| signal_classifier | 🟢 Complete | Implemented + hardened + observer hook — 44 tests passing |
| control_plane | 🟢 Complete | ControlStore + SignalJournal + SignalObserver — 28 tests passing |
| dashboard | 🟢 Complete | Operator UX console (Flask + HTML) — 17 tests passing |

**Layer status: 🟢 Complete — 195/195 tests passing**

---

## Sub-module Detail

### slack — 🟢 Complete
**Design complete. Implemented, tested, and hardened.**

**What is built:**
- Webhook endpoint (`/slack/events`) — returns 200, enqueues asynchronously; 503 on queue failure
- `reaction_added` emoji gate + `reaction_removed` toggle for signal withdrawal
- Bot message listener using the `is_bot` flag, scoped to monitored channels
- Signal normalisation into canonical schema v1.0 (thread context captured)
- NLP handshake path deferred as designed

**Hardening (2026-07-08):**
- Slack request signature verification — HMAC-SHA256 over `v0:{ts}:{body}`, 5-minute replay window, 401 on failure; skipped when no signing secret is configured (backward compatible)
- Multi-reaction race fixed — removal now matches on (`message_ts`, `reactor_id`) via new optional `SlackMetadata.reactor_id`, so one user withdrawing no longer drops another's signal

---

### gus — 🟢 Complete
**Design complete. Implemented, tested, and hardened.**

**What is built:**
- Webhook endpoint for `epic.*`, `td.*`, `escalation.created`, `task.blocked` (status-gated), and the `aquarium-intake` manual label
- `GusPoller` fallback (5-minute interval) with watermark cursor and crash-safe per-item advancement
- 60-second rapid-update collapse per item
- Signal normalisation into canonical schema v1.0

**Hardening (2026-07-08):**
- Polling task gate — task items produce a `task.blocked` signal only when `status == "Blocked"`; other tasks and unknown item types are skipped (previously mis-typed as `task.blocked`/`epic.updated`)
- `RateLimiter` — min-interval throttle with injectable clock/sleep, wired into the poller per `MAX_REQUESTS_PER_SECOND`
- `FileCursorStore` — durable cursor with atomic JSON write and epoch fallback on missing/malformed file (survives restart)

---

### google_workspace — 🟢 Complete
**Design complete. Implemented, tested, and hardened.**

**What is built:**
- Drive watch webhook (`/gdrive/notify`) on the intake folder with `[READY]` suffix gate (both conditions required)
- `WatchChannelRenewer` (6-day renewal ahead of 7-day expiry) and `GWSPoller` fallback (15-minute interval, cursor persistence)
- Document body fetch, 5-minute file-id dedup, 403 log-and-skip handling
- Signal normalisation into canonical schema v1.0

**Hardening (2026-07-08):**
- `page_token` now advanced after each `list_changes` (backward-compatible dict/tuple/legacy-list return shapes) to stop duplicate processing on clustered notifications
- Watch-channel renewal failures caught — logged, expiry not advanced, retried next cycle instead of propagating

---

### signal_classifier — 🟢 Complete
**Design complete. Implemented, tested, and hardened.**

**What is built:**
- Queue consumer (pull-based, batch 10) running the 4-step pipeline
- Step 1: rule-based pre-filter (empty/short content, spam bots, GUS task noise, stale signals)
- Step 2: semantic dedup (embedding cosine > 0.92, 2-hour window, canonical-id linking)
- Step 3: LLM classification (versioned prompt, daily token budget, 3-retry exponential backoff, park-on-unavailability)
- Step 4: forward `relevant` / drop `irrelevant` / park `uncertain` to the human review queue
- Observability metrics instrumented across the pipeline

**Hardening (2026-07-08):**
- `pre_filter_hit` now populated on dropped/parked signals (audit trail of which rule fired)
- Date-aware, injectable token budget (`TokenBudgetStore` + `InMemoryTokenBudgetStore`) — resets at day boundaries, swappable for a durable backend
- Explicit raw_content truncation before the LLM call (`max_llm_content_chars`); the stored signal is never mutated

**Design updates (2026-07-08):**
- Accepts an injectable `SignalObserver` (default `NullObserver`) and emits detected/dropped/duplicate/parked/review/work_item lifecycle events, decoupling the classifier from the dashboard.

**Deferred (external responsibilities, by design):** 4-hour SLA escalation, 50-example feedback-loop trigger, moderator Slack DM, and real LLM/embedding/store backends (currently in-memory stubs behind ABCs).

---

### control_plane — 🟢 Complete
**Shared runtime state for feed-layer configuration and observability.**

**What is built:**
- `ControlStore` — editable config: which Slack channels to sniff (`{id, name, enabled}` toggles) and which GUS teams to follow. Thread-safe, JSON-persisted with atomic write, and **fail-open** (empty config = allow everything), so wiring it into a connector never changes behaviour until an operator narrows scope.
- `SignalJournal` — bounded, thread-safe record of every detected signal and its terminal outcome (`work_item` / `dropped` / `duplicate` / `review` / `parked`). Powers the dashboard's feed, work-item list, and rollup counts.
- `SignalObserver` / `NullObserver` / `JournalObserver` — the classifier hook that populates the journal.

**Role:** the contract the connectors, classifier, and dashboard all depend on.

---

### dashboard — 🟢 Complete
**Operator UX console for the feed layer.**

**What is built:**
- `create_dashboard_app(control_store, journal)` — Flask app, single self-contained HTML page (inline CSS + vanilla JS, no build step; matches the repo's Flask+stdlib stack).
- Per-module status cards (Slack / GUS / Google Workspace): enabled state, detected count, work-item count, last-detected time, scope.
- Live **Detected Signals** feed with source and outcome filters, and a **Work Items** list — both polled every ~4s.
- Config panels: toggle Slack channels on/off, add/remove; add/remove followed GUS teams. Edits write straight to the shared `ControlStore`, changing what the connectors ingest.
- JSON API: `/api/status`, `/api/signals`, `/api/work-items`, `/api/config/slack/channels`, `/api/config/gus/teams`.

**Scope:** feed layer only for now, as requested. **Deferred:** dashboard auth, websocket push (polling for now), editing the GWS intake folder from the UI, and dashboards for the other three layers.

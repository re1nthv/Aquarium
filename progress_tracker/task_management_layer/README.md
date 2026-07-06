# Progress — Task Management Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| signal_router | 🔴 Not Started | Team assignment + orphan handling |
| orphan_signal_store | 🔴 Not Started | Wait queue for unroutable signals |
| team_log_buffer | 🔴 Not Started | Buffer + manual review surface |
| epic_grouping | 🔴 Not Started | Signal-to-epic mapping |
| story_shuffler | 🔴 Not Started | Dependency graph management |
| epic_reorganiser | 🔴 Not Started | Priority reranking |
| storage | 🔴 Not Started | Persistent state store |

**Layer status: 🔴 Not Started**

---

## Sub-module Detail

### signal_router — 🔴 Not Started
**What needs to be built:**
- Model/logic to evaluate incoming signal and assign it to a team
- Team routing dispatch to the correct team_log_buffer
- Orphan detection and forwarding to orphan_signal_store

**Pending:** Everything

---

### orphan_signal_store — 🔴 Not Started
**What needs to be built:**
- Persistent wait queue for unroutable signals
- Human moderator review interface
- Auto-routing retry logic when new team context becomes available

**Pending:** Everything

---

### team_log_buffer — 🔴 Not Started
**What needs to be built:**
- Per-team buffer/queue for incoming signals
- Manual review UI/surface for team members to accept, reject, or reroute signals
- Feedback capture mechanism that feeds back into the routing/classification model

**Pending:** Everything

---

### epic_grouping — 🔴 Not Started
**What needs to be built:**
- Model/logic to map a signal to an existing epic or identify need for a new epic
- Realtime and periodic processing modes
- Output tagging of each signal with its target epic

**Pending:** Everything

---

### story_shuffler — 🔴 Not Started
**What needs to be built:**
- Delta detection: is this task new or an update to an existing one?
- Duplicate detection and deduplication
- Dependency graph construction and full-rewrite on each change
- Realtime and periodic processing modes

**Pending:** Everything

---

### epic_reorganiser — 🔴 Not Started
**What needs to be built:**
- Priority scoring model for epics
- Trigger detection for strong signals (escalations, prod breaks) that force a rerank
- Periodic rerank schedule
- Output: freshly ordered epic list for the team's release

**Pending:** Everything

---

### storage — 🔴 Not Started
**What needs to be built:**
- Persistent store for compiled task/epic/dependency-graph state
- Query interface for the Task Executor Layer to read from
- Support for both realtime and periodic write modes

**Pending:** Everything

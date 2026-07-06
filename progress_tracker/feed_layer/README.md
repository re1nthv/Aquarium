# Progress — Feed Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| slack | 🔴 Not Started | Emoji-based marking, signal detection |
| gus | 🔴 Not Started | Epic/task/TD/escalation ingestion |
| google_workspace | 🔴 Not Started | PRD doc detection, ready-label gate |
| signal_classifier | 🔴 Not Started | Relevance classification gate |

**Layer status: 🔴 Not Started**

---

## Sub-module Detail

### slack — 🔴 Not Started
**What needs to be built:**
- Slack event listener / bot integration
- Detection of the dedicated marking emoji on messages/threads
- Signal type classifier (break vs. feature request vs. bot report vs. handshake)
- Forwarding of marked signals to the signal_classifier

**Pending:** Everything

---

### gus — 🔴 Not Started
**What needs to be built:**
- GUS API / webhook integration
- Handlers for epic create, task CRUD, TD creation, escalation events
- Manual labelling path alongside automated detection
- Forwarding of relevant signals to the signal_classifier

**Pending:** Everything

---

### google_workspace — 🔴 Not Started
**What needs to be built:**
- Google Workspace API integration (Docs/Sheets/Slides)
- Detection of the "ready" label/marker on documents
- Guard against consuming half-baked documents
- Forwarding of ready documents to the signal_classifier

**Pending:** Everything

---

### signal_classifier — 🔴 Not Started
**What needs to be built:**
- Classification model/prompt that evaluates relevance of each incoming signal
- Drop or park logic for irrelevant signals
- Forwarding of relevant signals downstream to Task Management Layer

**Pending:** Everything

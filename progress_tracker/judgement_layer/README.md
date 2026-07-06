# Progress — Judgement Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| task_status | 🔴 Not Started | Individual task outcome tracking |
| epic_status | 🔴 Not Started | Aggregated epic outcome tracking |
| delivery_tracker | 🔴 Not Started | Last-mile delivery validation |

**Layer status: 🔴 Not Started**

---

## Sub-module Detail

### task_status — 🔴 Not Started
**What needs to be built:**
- Outcome recorder for each executed task (success / failure)
- Integration with the dependency graph to unblock downstream tasks on success
- Feed of task-level outcomes up to epic_status

**Pending:** Everything

---

### epic_status — 🔴 Not Started
**What needs to be built:**
- Aggregation logic over constituent task statuses per epic
- Status states: complete, partially complete, blocked, failed
- Feed of epic-level outcomes up to delivery_tracker

**Pending:** Everything

---

### delivery_tracker — 🔴 Not Started
**What needs to be built:**
- End-to-end delivery validation — confirms tasks/epics shipped beyond just being internally closed
- Release-level dashboard: delivered vs. in-flight
- Integration with Epic Status and Task Status as inputs
- Scope binding to the team's current release

**Pending:** Everything

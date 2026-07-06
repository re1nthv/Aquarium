# Progress — Task Executor Layer

Parent: [progress_tracker/](../README.md)

| Sub-module | Status | Notes |
|---|---|---|
| executor | 🔴 Not Started | Core execution loop over epics |
| coding_tasks | 🔴 Not Started | Repo-scoped code implementation |
| operations_tasks | 🔴 Not Started | Autonomy-gated ops dispatch |
| manual_layer | 🔴 Not Started | Human-in-the-loop interface |

**Layer status: 🔴 Not Started**

---

## Sub-module Detail

### executor — 🔴 Not Started
**What needs to be built:**
- Loop logic to traverse all epics in the current release
- Dependency-graph reader to identify which tasks are unblocked
- Task type classifier (coding vs. operations)
- Dispatch to coding_tasks or operations_tasks
- Rate limiting governed by token budget, API limits, and task priority
- Realtime and periodic execution modes

**Pending:** Everything

---

### coding_tasks — 🔴 Not Started
**What needs to be built:**
- Repo identification logic — maps a task to the correct repository and branch
- Code implementation agent scoped to that repo
- Guard against cross-repo contamination
- Status reporting back to the Executor on completion/failure

**Pending:** Everything

---

### operations_tasks — 🔴 Not Started
**What needs to be built:**
- Autonomy level configuration per task or task type
- High-autonomy path: agent executes the operations task directly
- Low-autonomy path: routes to manual_layer
- Status reporting back to the Executor on completion/failure

**Pending:** Everything

---

### manual_layer — 🔴 Not Started
**What needs to be built:**
- Interface to surface tasks to a human operator clearly
- Notification/alerting mechanism for pending manual tasks
- Outcome capture (done/failed/deferred) fed back into the system
- Unblocking of downstream dependencies on outcome capture

**Pending:** Everything

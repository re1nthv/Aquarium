# Task Executor Layer

Reads the structured, prioritised work from the Task Management Layer and executes it. The executor loops through epics, unblocks tasks whose dependencies are satisfied, determines the nature of each task, and dispatches it to the appropriate handler — automated agent or human.

Execution rate is not unconstrained — token budget, API rate limits, and task priority all govern how fast work moves through this layer.

---

## Sub-modules

### [executor/](executor/)
The core execution loop that drives all task processing.

- Loops through all epics in scope for the team's current release.
- For each epic, identifies the next task whose parent dependencies have all completed successfully.
- Determines whether the task is a coding task or an operations task.
- Dispatches coding tasks to `coding_tasks/` and operations tasks to `operations_tasks/`.
- Operates in realtime or periodic mode.

**Key idea:** Execution rate is governed by token budget, rate limits, and task priority — not every task fires at full speed.

---

### [coding_tasks/](coding_tasks/)
Handles execution of tasks that are coding in nature.

- Receives coding tasks from the Executor.
- Identifies the appropriate repository where the task needs to be developed.
- Executes the implementation scoped to the correct codebase and branch.
- Confirms the correct repo before acting to avoid cross-repo contamination.

---

### [operations_tasks/](operations_tasks/)
Handles execution of tasks that are operational (non-coding) in nature.

- Receives operations tasks from the Executor.
- Based on the configured autonomy level, delegates the task either to the agent (automated) or to the Manual Layer (human).
- **High autonomy:** agent executes the task directly.
- **Low autonomy:** task is routed to the Manual Layer.

---

### [manual_layer/](manual_layer/)
The human-in-the-loop interface for tasks requiring manual intervention.

- Surfaces tasks that the agent cannot or should not execute autonomously.
- Presents them clearly to a human operator for action.
- Feeds the outcome back into the system so downstream dependencies can unblock.
- Used for low-autonomy operations tasks and any task flagged by the Executor as requiring human judgement.

---

## Data Flow

```
Task Management Layer (Storage)
    │
    ▼
Executor (loop over epics, find unblocked tasks)
    │
    ├──► Coding Tasks ──► correct repo / branch
    │
    └──► Operations Tasks
              │
              ├──► Agent (high autonomy)
              │
              └──► Manual Layer (low autonomy) ──► human action ──► unblocks downstream
```

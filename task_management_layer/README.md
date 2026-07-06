# Task Management Layer

Receives classified signals from the Feed Layer and transforms them into structured, prioritised, dependency-aware tasks and epics. This layer is the brain of the system — it organises work before any execution begins.

Every step in this layer has a manual review option. The long-term vision is a trainable model where teams debate, rank, and correct the system's decisions rather than blindly accepting them.

---

## Sub-modules

### [signal_router/](signal_router/)
Routes incoming signals to the correct team's buffer.

- A model evaluates each signal and dispatches it to the relevant team's log buffer.
- Signals at this stage are unpolished — not yet structured into tasks.
- **Team Router:** determines which team owns the signal.
- **Orphan Signal Handler:** signals with no determinable team are forwarded to the Orphan Signal Store.

---

### [orphan_signal_store/](orphan_signal_store/)
Holds signals that could not be routed to any known team.

- Parks unroutable signals in a wait queue.
- Signals remain here until the correct team is identified automatically, or a human moderator routes them manually.
- Nothing is silently dropped — every signal is accounted for.

---

### [team_log_buffer/](team_log_buffer/)
Maintains a buffer of all signals routed to a specific team.

- Holds the incoming stream of signals before further processing.
- Provides a surface for the team to manually review queued signals.
- Allows team members to give feedback to the system, which feeds back into improving routing and classification accuracy over time.

---

### [epic_grouping/](epic_grouping/)
Groups processed signals into existing epics or creates new ones.

- Processes signals from the team log buffer and maps them to the appropriate epic.
- Determines whether a signal fits an existing epic or requires a new one to be created.
- Operates in realtime or periodic mode depending on configuration.

---

### [story_shuffler/](story_shuffler/)
Manages placement, ordering, and dependency tracking of stories/tasks within an epic.

- Determines if an incoming task is a delta under an existing epic or a net-new item.
- Finds the correct epic for the task and checks for duplicates before inserting.
- Updates and rewrites the full dependency graph of tasks within the epic after every change.
- Operates in realtime or periodic mode.

---

### [epic_reorganiser/](epic_reorganiser/)
Reorganises and reranks the priority and execution order of all epics under a team.

- Triggered by strong incoming signals (e.g. production breaks, customer escalations) or on a periodic schedule.
- Reorders epics to reflect the sequence in which they should be executed.
- Outputs a freshly ranked epic list for the team's current release.

---

### [storage/](storage/)
Persists the compiled state of all tasks, epics, dependency graphs, and signals.

- Serves as the source of truth for the Task Executor Layer.
- Supports both realtime and periodic processing modes with a consistent, queryable state.

---

## Data Flow

```
Feed Layer
    │
    ▼
Signal Router ──► Orphan Signal Store (unroutable signals)
    │
    ▼
Team Log Buffer (manual review + feedback)
    │
    ▼
Epic Grouping
    │
    ▼
Story Shuffler (dependency graph)
    │
    ▼
Epic Reorganiser (priority ranking)
    │
    ▼
Storage ──► Task Executor Layer
```

---

## Ideas

- Every step should have a human-in-the-loop option, especially early on.
- The system should be trainable: teams should be able to debate and correct the model's grouping, ranking, and prioritisation decisions so the model improves over time rather than being a fixed ruleset.

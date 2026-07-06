# Judgement Layer

The final layer in the system. Responsible for determining whether epics and tasks have truly been completed and delivered — not just marked done internally, but shipped to the last mile of delivery.

This layer provides release-level visibility across the entire team's work and is the authoritative source of truth for what has shipped vs. what is still in flight.

---

## Sub-modules

### [task_status/](task_status/)
Tracks and evaluates the final status of individual tasks after execution.

- Determines whether a given task completed successfully or failed.
- Records the outcome and makes it available to the dependency graph so downstream tasks can unblock.
- Feeds task-level outcomes up to Epic Status for aggregation.

---

### [epic_status/](epic_status/)
Tracks and evaluates the overall status of epics based on their constituent tasks.

- Aggregates task-level outcomes to determine whether an epic is complete, partially complete, blocked, or failed.
- Reports epic status up to the Delivery Tracker for release-level visibility.

---

### [delivery_tracker/](delivery_tracker/)
The final mile checkpoint — validates that completed work has actually been delivered end-to-end.

- Confirms that epics and tasks marked done have been shipped to their intended destination, not just internally closed.
- Provides a release-level view of what has been delivered vs. what is still in flight.
- Scope: all epics under the current release for the team.
- Works in conjunction with Epic Status and Task Status to give a complete picture of delivery health.

---

## Data Flow

```
Task Executor Layer
    │
    ▼
Task Status (individual task outcomes)
    │
    ▼
Epic Status (aggregated epic outcomes)
    │
    ▼
Delivery Tracker (did it ship end-to-end?)
```

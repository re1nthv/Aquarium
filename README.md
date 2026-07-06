# Aquarium

An autonomous pipeline that ingests signals from multiple channels (Slack, GUS, Google Workspace), transforms them into structured and prioritised tasks, executes them with configurable autonomy, and judges whether they were truly delivered end-to-end.

The system is built around four sequential layers, each owning a distinct phase of the pipeline. Every layer maintains a human-in-the-loop option — the system is designed to be trainable and correctable, not a black box.

---

## Architecture Overview

```
External Channels (Slack, GUS, Google Workspace)
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ Feed Layer                                          │
│  ├── slack/            (production breaks, feature  │
│  │                      requests, bot reports,      │
│  │                      informal handshakes)        │
│  ├── gus/              (epics, tasks, TDs,          │
│  │                      escalations)               │
│  ├── google_workspace/ (PRDs marked ready)          │
│  └── signal_classifier/(relevance gate — filters   │
│                          noise before downstream)  │
└─────────────────────────────────────────────────────┘
    │  only relevant signals pass
    ▼
┌─────────────────────────────────────────────────────┐
│ Task Management Layer                               │
│  ├── signal_router/        (team assignment)        │
│  ├── orphan_signal_store/  (unroutable signals)     │
│  ├── team_log_buffer/      (manual review + feedback│
│  ├── epic_grouping/        (map signals to epics)   │
│  ├── story_shuffler/       (dependency graph)       │
│  ├── epic_reorganiser/     (priority ranking)       │
│  └── storage/              (source of truth)        │
└─────────────────────────────────────────────────────┘
    │  structured, prioritised, dependency-aware tasks
    ▼
┌─────────────────────────────────────────────────────┐
│ Task Executor Layer                                 │
│  ├── executor/         (core execution loop)        │
│  ├── coding_tasks/     (repo-scoped implementation) │
│  ├── operations_tasks/ (autonomy-gated ops work)    │
│  └── manual_layer/     (human-in-the-loop)          │
└─────────────────────────────────────────────────────┘
    │  completed task outcomes
    ▼
┌─────────────────────────────────────────────────────┐
│ Judgement Layer                                     │
│  ├── task_status/      (individual task outcomes)   │
│  ├── epic_status/      (aggregated epic outcomes)   │
│  └── delivery_tracker/ (last-mile delivery check)  │
└─────────────────────────────────────────────────────┘
```

---

## Layers

### [feed_layer/](feed_layer/)
The entry point. Ingests raw signals from Slack, GUS, and Google Workspace. A signal classifier acts as the first gate, filtering out noise before anything reaches the rest of the system.

Key sub-modules: `slack`, `gus`, `google_workspace`, `signal_classifier`

**Critical constraint:** Over-ingestion is expensive. Every signal must pass the classifier before entering the pipeline.

---

### [task_management_layer/](task_management_layer/)
The brain. Transforms classified signals into structured, prioritised, dependency-aware tasks grouped under epics. Routes signals to the right team, parks orphans, maintains a team log buffer for manual review, builds and rewrites dependency graphs, and reranks epics when strong signals arrive.

Key sub-modules: `signal_router`, `orphan_signal_store`, `team_log_buffer`, `epic_grouping`, `story_shuffler`, `epic_reorganiser`, `storage`

**Critical constraint:** This layer must be trainable. Teams should debate and correct grouping, ranking, and prioritisation decisions to teach the model — not just accept its output blindly.

---

### [task_executor_layer/](task_executor_layer/)
The hands. Loops through prioritised epics, unblocks tasks whose dependencies are satisfied, and dispatches them — coding tasks to the right repo, operations tasks either to the agent or to a human based on autonomy level.

Key sub-modules: `executor`, `coding_tasks`, `operations_tasks`, `manual_layer`

**Critical constraint:** Execution rate is governed by token budget, rate limits, and task priority — not unconstrained throughput.

---

### [judgement_layer/](judgement_layer/)
The final checkpoint. Evaluates whether tasks and epics were truly delivered end-to-end, not just internally closed. Provides release-level visibility across the team's work.

Key sub-modules: `task_status`, `epic_status`, `delivery_tracker`

**Critical constraint:** Completion means shipped to the last mile — not just marked done in the system.

---

## Design Principles

- **Human in the loop at every layer** — no layer is fully autonomous by default. Manual review surfaces are built in throughout.
- **Signal quality over volume** — the system is designed to be selective. Reading everything is prohibitive; the classifier and manual gates enforce discipline.
- **Trainable, not fixed** — grouping, ranking, and routing decisions should improve over time through team feedback, not be locked into static rules.
- **Dependency-aware execution** — tasks only execute when their dependencies are satisfied. The dependency graph is the source of execution order, not arbitrary sequencing.
- **Last-mile accountability** — a task is not done until it has been delivered, not just closed.

# Storage

Persists the compiled state of tasks, epics, dependency graphs, and signals for the task management layer.

## Responsibility

- Store the output of compile-time task processing — the current state of all epics, stories, and their dependency graphs.
- Serve as the source of truth for the Task Executor Layer to read from when determining what to execute next.
- Support both the realtime and periodic processing modes by maintaining a consistent and queryable state.

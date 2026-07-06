# Operations Tasks

Handles execution of tasks that are operational in nature (non-coding).

## Responsibility

- Receive operations tasks from the Executor.
- Based on the configured autonomy level, decide whether the task is delegated to the agent for automated execution or routed to the Manual Layer for human action.

## Autonomy Levels

- **High autonomy** — the agent executes the operations task directly.
- **Low autonomy** — the task is handed off to a human via the Manual Layer.

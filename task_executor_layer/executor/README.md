# Executor

The core loop that drives task execution across all epics in the current release.

## Responsibility

- Loop through all epics in scope for the team's current release.
- For each epic, identify the next task whose parent dependencies have all been completed successfully.
- Determine the nature of the task (coding vs. operations) and dispatch it to the appropriate handler.

## Behaviour (Realtime / Periodic)

- Operates in realtime or periodic mode based on configuration.
- Rate of execution is governed by factors such as token budget, API rate limits, and task priority.

## Ideas

- The execution rate needs to be carefully managed — not every task should be fired at full speed. Token budget, rate limits, and priority should all be considered when deciding what runs next.

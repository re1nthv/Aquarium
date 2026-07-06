# Story Shuffler

Manages the placement and ordering of stories/tasks within their epic.

## Responsibility

- Determine whether an incoming task is a delta (incremental change) under an existing epic or a completely new epic item.
- Find the correct epic for the task to be placed under.
- Check for duplicate tasks and avoid adding redundant work items.
- Update the dependency graph of tasks within the epic.
- Rewrite the complete state of the dependency graph after each change.

## Behaviour (Realtime / Periodic)

- Operates either in realtime or periodic mode depending on configuration.
- Ensures the dependency graph under each epic always reflects an accurate and up-to-date execution order.

# Manual Layer

The human-in-the-loop interface for tasks that require manual intervention.

## Responsibility

- Receive tasks that the agent cannot or should not execute autonomously.
- Surface them clearly to a human operator for action.
- Feed the outcome back into the system so downstream dependencies can unblock.

## When It Is Used

- Operations tasks where the autonomy level is set to low.
- Any task flagged by the Executor as requiring human judgement before proceeding.

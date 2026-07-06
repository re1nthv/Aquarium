# Epic Reorganiser

Reorganises and reranks the priority and execution order of epics when a strong signal warrants it.

## Responsibility

- Continuously evaluate the relative priority of all epics under a team.
- When a strong incoming signal (e.g. a customer escalation, a production break) arrives, rerank epics accordingly.
- Reorder epics to reflect the sequence in which they should be executed.

## Behaviour (Realtime / Periodic)

- Triggered by high-priority signals or on a periodic schedule.
- Output: a freshly ranked and ordered list of epics for the team's release.

# GUS Feed

Responsible for ingesting and processing signals originating from GUS work items.

## Signal Types

- **New epics with user stories** — a person creates a new epic with detailed user stories under it and marks it for the current release under your team.
- **Task create/update/delete under existing epics** — any CRUD operation on tasks beneath an existing epic needs to be handled.
- **Team dependencies (TDs)** — a completely different team creates a TD on your team and is proactively waiting on it.
- **Customer escalations** — escalations that arrive through GUS and need to be triaged and actioned.

## Ideas

- Taking care of every GUS signal can be very compute-heavy. Support both an automated detection path and a manual labelling/annotation path so teams can control the intake volume.

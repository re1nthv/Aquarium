# Slack Feed

Responsible for ingesting and surfacing relevant signals from Slack channels.

## Signal Types

- **Production breaks** — a random person reports something broken in prod; needs an immediate fix ranging from an emergency patch to a leisured fix.
- **Feature requests** — typically from a customer or stakeholder, a new feature request to be taken up in an upcoming release.
- **Bot-reported bugs** — a channel bot periodically reports all bugs detected across all environments.
- **Informal handshakes** — within a Slack thread, two parties have an informal agreement over work that needs to be done; these need to be identified and converted to tasks.

## Ideas

- Introduce a dedicated emoji that team members can use to mark any message/thread as a probable feed for the pipeline to consume. This acts as the first manual gate before a signal enters the system.

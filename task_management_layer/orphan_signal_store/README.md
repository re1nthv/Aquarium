# Orphan Signal Store

Holds signals that could not be routed to any known team.

## Responsibility

- Accept signals that the Signal Router could not assign to a team.
- Park them in a wait queue until one of the following happens:
  - The correct team is identified automatically.
  - A human moderator reviews the signal and manually routes it.

## Behaviour

- Signals remain here until resolved — they are never silently dropped.
- Supports human-in-the-loop review to handle edge cases the model cannot confidently resolve.

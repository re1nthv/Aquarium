# Epic Grouping

Groups processed signals into their respective epics, or identifies when a new epic needs to be created.

## Responsibility

- Process signals from the team log buffer and group them into the epics they belong to.
- Determine whether a signal maps to an existing epic or warrants the creation of a new one.

## Behaviour (Realtime / Periodic)

- Depending on the configured variant, signals are processed either in realtime as they arrive or in periodic batches.
- Output: each signal is tagged with the epic it belongs to and forwarded to the Story Shuffler.

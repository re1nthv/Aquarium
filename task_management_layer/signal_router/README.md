# Signal Router

Routes incoming signals to the correct team buffer.

## Responsibility

- For a given incoming signal, the model determines which team it belongs to and dispatches it to that team's buffer.
- Signals at this stage are unpolished — they have not yet been refined into structured tasks.

## Sub-components

- **Team Router** — evaluates the signal and routes it to the relevant team's log buffer.
- **Orphan Signal Handler** — if no team can be determined, the signal is forwarded to the Orphan Signal Store.

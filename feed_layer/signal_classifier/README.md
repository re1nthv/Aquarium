# Signal Classifier

Acts as the top-level gate over all incoming signals before they enter the pipeline.

## Responsibility

- Evaluate every incoming signal (from Slack, GUS, Google Workspace, or any future channel) and determine whether it is relevant enough to be passed downstream.
- Prevent over-consumption: reading every Slack message, every GUS update, or every Workspace doc would be extremely taxing on token burns, compute, and storage. This layer filters noise before it reaches the rest of the system.

## Behaviour

- Classify each signal as relevant or irrelevant.
- Only relevant signals are forwarded to the Task Management Layer.
- Irrelevant signals are dropped or parked for periodic human review.

# Feed Layer

The entry point of the entire system. Responsible for ingesting raw signals from all external channels, classifying them for relevance, and forwarding only meaningful signals downstream to the Task Management Layer.

The core concern of this layer is signal quality over signal volume — consuming everything indiscriminately would be computationally prohibitive. This layer acts as the first gate.

---

## Sub-modules

### [slack/](slack/)
Ingests signals from Slack channels.

- Production breaks reported by team members or customers needing immediate fixes.
- Feature requests from customers or stakeholders for upcoming releases.
- Bot-reported bugs detected periodically across all environments.
- Informal handshakes between two parties in a thread that imply work needs to be done.

**Key idea:** A dedicated emoji lets team members manually mark any message/thread as a feed candidate, acting as the first human gate before a signal enters the pipeline.

---

### [gus/](gus/)
Ingests signals from GUS work items.

- New epics created with user stories, marked for the current release under the team.
- CRUD operations (create/update/delete) on tasks under existing epics.
- Team dependencies (TDs) raised by other teams on your team, requiring action.
- Customer escalations arriving through GUS that need triage.

**Key idea:** Handling every GUS signal is compute-heavy. The system supports both automated detection and a manual labelling path so intake volume stays controlled.

---

### [google_workspace/](google_workspace/)
Ingests signals from Google Workspace documents (Docs, Sheets, Slides).

- New PRD documents authored and explicitly marked as ready for implementation.

**Key idea:** Only consume documents that are explicitly marked ready. Picking up half-baked documents mid-authoring leads to premature and wasteful task generation.

---

### [signal_classifier/](signal_classifier/)
The top-level classification gate over all incoming signals.

- Evaluates every signal from any channel and determines whether it is relevant enough to pass downstream.
- Prevents over-consumption: unfiltered ingestion of all Slack messages, GUS updates, or Workspace docs would be taxing on token budget, compute, and storage.
- Relevant signals are forwarded to the Task Management Layer; irrelevant signals are dropped or parked for periodic human review.

---

## Data Flow

```
Slack ──────┐
GUS ────────┤──► Signal Classifier ──► Task Management Layer
Google WS ──┘
```

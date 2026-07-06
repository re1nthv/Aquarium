# Google Workspace Feed

Responsible for ingesting signals from Google Workspace documents (Docs, Sheets, Slides).

## Signal Types

- **New PRD docs** — someone creates a new Product Requirements Document in Google Docs/Sheets/Slides and labels it as ready for implementation.

## Ideas

- Only consume documents that are explicitly marked as ready, rather than picking up half-baked documents mid-authoring. This avoids premature task generation from incomplete specs.

# Google Workspace Feed

Responsible for ingesting signals from Google Workspace documents and normalising them into the canonical Signal schema.

---

## Trigger Mechanism

The connector uses the **Google Drive API push notifications** (watch channels) to detect document changes, filtered by a concrete "ready" convention defined below.

### The "Ready" Convention (Resolved)

A document is considered ready for ingestion when **both** of the following are true:

1. **It lives in a designated intake folder** — a specific Google Drive folder shared with the Aquarium service account. Teams drop documents into this folder when they want them consumed. Folder ID is configured per deployment.
2. **Its title ends with the suffix ` [READY]`** — e.g. `Checkout v2 PRD [READY]`. This is the explicit human opt-in. Documents without this suffix in the intake folder are ignored.

Using both conditions (folder + title suffix) gives two independent gates: the folder prevents the connector from watching the entire Drive estate, and the title suffix prevents it from consuming a document that was placed in the folder prematurely.

**Why title suffix over a label/comment?**
- Drive labels require additional API scope and are less visible to document authors.
- A title suffix is immediately visible, easy to add/remove, and requires no extra permissions.
- A status comment or heading inside the document body was considered but rejected — it would require reading the full document content on every change event, which is expensive.

### Path 1 — Drive API Watch (primary)
- **API:** `drive.files.watch` on the intake folder, with `pageToken` for incremental change tracking.
- **Trigger condition:** A file change event is received (`kind: drive#change`) for a file in the intake folder where the file name ends with ` [READY]`.
- **Change types acted on:** `file.created`, `file.updated` (name or content changed). `file.deleted` is a no-op — deletion does not generate a downstream signal.
- **Watch channel expiry:** Google Drive watch channels expire after at most 7 days. The connector renews the watch channel every 6 days via a scheduled job to avoid expiry gaps.
- **What gets fetched on trigger:** The connector calls `files.get` to retrieve the document metadata (title, owner, last modified, sharing settings). It calls `docs.get` (for Docs) or reads the Sheet/Slide export to extract the document body as plain text. This fetch happens asynchronously after the watch event is received.

### Path 2 — Polling fallback
Used if the watch channel lapses or renewal fails.

- **Polling interval:** Every 15 minutes (documents change slowly relative to Slack/GUS).
- **Query:** `files.list` on the intake folder, filtered by `modifiedTime > {cursor}` and name contains `[READY]`.
- **Cursor:** Last successfully processed `modifiedTime`, persisted durably.

---

## Signal Schema Output

```
Signal {
  id:             uuid
  schema_version: "1.0"
  source:         "google_workspace"
  type:           "document.ready"
  raw_content:    string                    // plain text export of the document body
  timestamp:      ISO8601                   // document's lastModifiedTime
  ingested_at:    ISO8601
  originator:     { google_user_id, email } // last modifier of the document
  metadata: {
    file_id:        string                  // Google Drive file ID — primary dedup key
    file_name:      string                  // full title including [READY] suffix
    mime_type:      string                  // application/vnd.google-apps.document etc.
    folder_id:      string                  // intake folder ID
    doc_url:        string                  // direct link to the document
    sharing_scope:  "team" | "org" | "public"  // derived from sharing settings
  }
}
```

---

## Access Control

- The connector runs as a **dedicated Google service account** with read-only access to the intake folder.
- The service account does NOT have org-wide Drive access — it can only see files explicitly shared with it or placed in the intake folder.
- If a document's sharing settings restrict it to specific users, and the service account is not in that list, the `docs.get` call will fail with 403. The connector logs this as a `permission_denied` event and does not retry — a human must correct the sharing settings.
- Document content is treated as potentially sensitive. Raw content is not logged; only the signal metadata is written to logs.

---

## Delivery Guarantee

- Drive watch notifications are acknowledged immediately (HTTP 200). Document fetch and enqueue happen asynchronously.
- Enqueued signals are written to the same internal message queue as Slack and GUS signals.
- Watch channel renewal is a scheduled job independent of the ingest path — a renewal failure does not block in-flight signal processing, it only affects future events until polling kicks in.

---

## Deduplication Anchor

- `metadata.file_id` is the stable Google Drive file ID. Use this as the dedup key within the Google Workspace source.
- If the same document is modified multiple times before the classifier processes it, only the latest version (by `timestamp`) is forwarded — earlier versions within a 5-minute window are collapsed.
- Cross-source deduplication (e.g. a PRD mentioned in a Slack message and also in the intake folder) is handled by the Signal Classifier.

---

## What is NOT handled here

- Relevance classification — that is the Signal Classifier's job.
- Cross-source deduplication — handled at the classifier layer.
- Sheets or Slides beyond plain text export — structured data parsing is deferred.

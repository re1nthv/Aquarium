# Progress Tracker

Development-only module. Tracks the implementation status of every sub-module across the entire system. Any change — design update, implementation start, completion — must be reflected here and propagated up to the relevant layer status and this root dashboard.

See [CHANGELOG.md](CHANGELOG.md) for a running log of every change.

---

## Overall Status

| Layer | Status | Sub-modules |
|---|---|---|
| [Feed Layer](feed_layer/README.md) | 🟢 Complete | 4 / 4 complete — 92 tests passing |
| [Task Management Layer](task_management_layer/README.md) | 🔴 Not Started | 0 / 7 complete |
| [Task Executor Layer](task_executor_layer/README.md) | 🔴 Not Started | 0 / 4 complete |
| [Judgement Layer](judgement_layer/README.md) | 🔴 Not Started | 0 / 3 complete |

**Total: 0 / 18 sub-modules complete**

---

## Status Legend

| Icon | Meaning |
|---|---|
| 🔴 Not Started | No implementation work begun |
| 🟡 In Progress | Actively being worked on |
| 🟠 Blocked | Work started but blocked on a dependency or decision |
| 🟢 Complete | Implemented and verified |

---

## Feed Layer — 🟢 Complete
[Full detail →](feed_layer/README.md)

| Sub-module | Status |
|---|---|
| slack | 🟢 Complete — 17 tests passing |
| gus | 🟢 Complete — 23 tests passing |
| google_workspace | 🟢 Complete — 25 tests passing |
| signal_classifier | 🟢 Complete — 27 tests passing |

---

## Task Management Layer — 🔴 Not Started
[Full detail →](task_management_layer/README.md)

| Sub-module | Status |
|---|---|
| signal_router | 🔴 Not Started |
| orphan_signal_store | 🔴 Not Started |
| team_log_buffer | 🔴 Not Started |
| epic_grouping | 🔴 Not Started |
| story_shuffler | 🔴 Not Started |
| epic_reorganiser | 🔴 Not Started |
| storage | 🔴 Not Started |

---

## Task Executor Layer — 🔴 Not Started
[Full detail →](task_executor_layer/README.md)

| Sub-module | Status |
|---|---|
| executor | 🔴 Not Started |
| coding_tasks | 🔴 Not Started |
| operations_tasks | 🔴 Not Started |
| manual_layer | 🔴 Not Started |

---

## Judgement Layer — 🔴 Not Started
[Full detail →](judgement_layer/README.md)

| Sub-module | Status |
|---|---|
| task_status | 🔴 Not Started |
| epic_status | 🔴 Not Started |
| delivery_tracker | 🔴 Not Started |

---

## How to Update

When any sub-module changes status:

1. Update the sub-module row in the **layer README** (`progress_tracker/<layer>/README.md`)
2. Update the sub-module row in this **root dashboard** (above)
3. Recalculate the layer status (🔴 if all not started, 🟡 if any in progress, 🟢 only if all complete)
4. Recalculate the **Total** count at the top
5. Add a line to [CHANGELOG.md](CHANGELOG.md)

This ensures every ancestor node always reflects the true state of its sub-tree.

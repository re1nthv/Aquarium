"""Feed-layer control plane — shared editable config and observability record.

* ``ControlStore`` — which Slack channels to sniff, which GUS teams to follow.
* ``SignalJournal`` — the detected-signal / work-item record the dashboard reads.
* ``SignalObserver`` — the classifier hook that populates the journal.
"""

from .observer import JournalObserver, NullObserver, SignalObserver
from .store import (
    ControlStore,
    JournalEntry,
    SignalJournal,
    SlackChannel,
)

__all__ = [
    "ControlStore",
    "SlackChannel",
    "SignalJournal",
    "JournalEntry",
    "SignalObserver",
    "NullObserver",
    "JournalObserver",
]

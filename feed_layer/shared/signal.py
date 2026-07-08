from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional


Source = Literal["slack", "gus", "google_workspace"]

SlackSignalType = Literal["manual_reaction", "bot_message"]
GusSignalType = Literal[
    "epic.created", "epic.updated",
    "td.created", "td.updated",
    "escalation.created", "task.blocked", "manual_label",
]
GWSSignalType = Literal["document.ready"]
SignalType = SlackSignalType | GusSignalType | GWSSignalType

ClassificationResult = Literal["relevant", "irrelevant", "uncertain"]


@dataclass
class SlackMetadata:
    channel_id: str
    message_ts: str
    thread_ts: Optional[str] = None
    reaction: Optional[str] = None
    bot_name: Optional[str] = None
    reactor_id: Optional[str] = None
    """Slack user id of the reactor, for manual_reaction signals. Used to
    disambiguate multiple reactors on the same message_ts when a reaction is
    withdrawn (see feed_layer.slack.connector._remove_signal_by_ts)."""


@dataclass
class GusMetadata:
    gus_item_id: str
    gus_item_type: Literal["epic", "task", "td", "escalation"]
    gus_item_url: str
    team: str
    release: Optional[str] = None
    label_applied: Optional[str] = None


@dataclass
class GoogleWorkspaceMetadata:
    file_id: str
    file_name: str
    mime_type: str
    folder_id: str
    doc_url: str
    sharing_scope: Literal["team", "org", "public"]


@dataclass
class Originator:
    id: str
    name: str


@dataclass
class Classification:
    result: ClassificationResult
    prompt_version: str
    classified_at: datetime
    confidence: Optional[float] = None
    duplicate_of: Optional[str] = None
    pre_filter_hit: Optional[str] = None


@dataclass
class Signal:
    source: Source
    type: SignalType
    raw_content: str
    timestamp: datetime
    ingested_at: datetime
    originator: Originator
    metadata: SlackMetadata | GusMetadata | GoogleWorkspaceMetadata
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = "1.0"
    classification: Optional[Classification] = None

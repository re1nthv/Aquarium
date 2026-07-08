"""Signal Classifier — classifies incoming signals via a 4-step pipeline.

Pipeline per signal:
  1. pre_filter   — drop obvious noise by rule
  2. dedup_check  — semantic deduplication via embeddings
  3. llm_classify — LLM-based relevance classification
  4. route        — send to output_queue / human_queue / drop
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable, Optional

from feed_layer.shared import (
    Classification,
    MessageQueue,
    Signal,
)
from feed_layer.signal_classifier.metrics_names import (
    LLM_FALLBACK_COUNT,
    LLM_TOKENS_USED,
    SIGNALS_CLASSIFIED_IRRELEVANT,
    SIGNALS_CLASSIFIED_RELEVANT,
    SIGNALS_CLASSIFIED_UNCERTAIN,
    SIGNALS_DEDUPLICATED,
    SIGNALS_INGESTED,
    SIGNALS_PRE_FILTER_DROPPED,
    UNCERTAIN_QUEUE_AGE_P90,
    UNCERTAIN_QUEUE_DEPTH,
)


# ---------------------------------------------------------------------------
# Abstract interfaces
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    """Abstract LLM classification client."""

    @abstractmethod
    def classify(
        self, signal: Signal, prompt_version: str
    ) -> tuple[str, int, Optional[float]]:
        """Classify a signal.

        Returns:
            (result, tokens_used, confidence)
            result is one of "relevant", "irrelevant", "uncertain".
        """


class Embedder(ABC):
    """Abstract text embedder."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a dense embedding vector for *text*."""


class DedupStore(ABC):
    """Abstract store for deduplication embeddings."""

    @abstractmethod
    def find_similar(
        self, embedding: list[float], within_hours: float, threshold: float
    ) -> Optional[str]:
        """Return the canonical signal_id of a similar stored signal, or None."""

    @abstractmethod
    def store(
        self, signal_id: str, embedding: list[float], timestamp: datetime
    ) -> None:
        """Persist an embedding so future calls to find_similar can detect it."""

    @abstractmethod
    def mark_dropped(self, signal_id: str) -> None:
        """Record that a signal was dropped (irrelevant classification)."""


class HumanReviewQueue(ABC):
    """Abstract human-review queue."""

    @abstractmethod
    def add(self, signal: Signal, reason: str) -> None:
        """Submit a signal for human review with a reason label."""

    @abstractmethod
    def depth(self) -> int:
        """Return the current number of signals awaiting review."""

    @abstractmethod
    def age_p90_seconds(self) -> float:
        """Return the 90th-percentile age (in seconds) of items in the queue."""


class MetricsCollector(ABC):
    """Abstract metrics sink."""

    @abstractmethod
    def increment(
        self, metric: str, value: int = 1, tags: Optional[dict] = None
    ) -> None:
        """Increment a counter metric."""

    @abstractmethod
    def gauge(
        self, metric: str, value: float, tags: Optional[dict] = None
    ) -> None:
        """Record a gauge metric."""


class TokenBudgetStore(ABC):
    """Abstract store for per-day LLM token usage.

    Keying usage by calendar day (rather than an in-memory counter that
    only resets on process restart) makes the daily budget durable across
    restarts and swappable for a real backend (Redis, a DB row, etc.).
    """

    @abstractmethod
    def get_tokens_used(self, day: str) -> int:
        """Return the number of tokens consumed so far on *day* (YYYY-MM-DD)."""

    @abstractmethod
    def add_tokens(self, day: str, n: int) -> None:
        """Record *n* additional tokens consumed on *day* (YYYY-MM-DD)."""


class InMemoryTokenBudgetStore(TokenBudgetStore):
    """Default in-memory token budget store, keyed by ISO date string.

    Usage lives for the lifetime of the process (same as the old
    ``_tokens_used_today`` counter), but is now keyed by day so it can be
    swapped for a durable backend without changing classifier code, and so
    a day boundary naturally resets the count for a fresh key.
    """

    def __init__(self) -> None:
        self._usage: dict[str, int] = {}

    def get_tokens_used(self, day: str) -> int:
        return self._usage.get(day, 0)

    def add_tokens(self, day: str, n: int) -> None:
        self._usage[day] = self._usage.get(day, 0) + n


def _utc_today_iso() -> str:
    """Default clock — today's date (UTC) as an ISO ``YYYY-MM-DD`` string."""
    return datetime.now(tz=timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ClassifierConfig:
    batch_size: int = 10
    prompt_version: str = "v1"
    spam_bot_names: list[str] = field(
        default_factory=lambda: ["giphy", "polly"]
    )
    daily_token_budget: int = 100_000
    # Approximate char-based cap standing in for "~2,000 tokens" (README),
    # using a rough 4 chars/token heuristic. Only the copy of raw_content
    # handed to the LLM is truncated; the stored Signal is never mutated.
    max_llm_content_chars: int = 8_000
    # Swappable persistence for daily token usage — defaults to an
    # in-memory store so existing callers/tests see identical behaviour.
    token_budget_store: TokenBudgetStore = field(
        default_factory=InMemoryTokenBudgetStore
    )
    # Injectable clock returning today's date as an ISO string. Defaults to
    # real UTC "today" so budget accounting resets at real date boundaries;
    # tests can override this to simulate crossing a day boundary.
    today: Callable[[], str] = field(default=_utc_today_iso)
    # Deprecated / vestigial: retained only so any code that pokes this
    # private attribute directly (e.g. older tests) doesn't break. It is no
    # longer read by daily_token_budget_remaining()/consume_tokens(), which
    # now delegate to token_budget_store keyed by today().
    _tokens_used_today: int = field(default=0, repr=False)

    def daily_token_budget_remaining(self) -> int:
        """Return how many tokens remain in today's budget."""
        used = self.token_budget_store.get_tokens_used(self.today())
        remaining = self.daily_token_budget - used
        return max(0, remaining)

    def consume_tokens(self, n: int) -> None:
        """Deduct *n* tokens from today's budget."""
        self.token_budget_store.add_tokens(self.today(), n)


# ---------------------------------------------------------------------------
# In-memory implementations (for testing)
# ---------------------------------------------------------------------------


class InMemoryLLMClient(LLMClient):
    """Configurable stub LLM — returns a preset sequence of responses."""

    def __init__(
        self,
        responses: Optional[list[tuple[str, int, Optional[float]]]] = None,
    ) -> None:
        # Each element: (result, tokens_used, confidence)
        # If the list is exhausted, default to ("relevant", 100, 0.9)
        self._responses: list[tuple[str, int, Optional[float]]] = (
            responses if responses is not None else []
        )
        self._call_count = 0

    def classify(
        self, signal: Signal, prompt_version: str
    ) -> tuple[str, int, Optional[float]]:
        if self._call_count < len(self._responses):
            response = self._responses[self._call_count]
            self._call_count += 1
            if isinstance(response, Exception):
                raise response
            return response
        self._call_count += 1
        return ("relevant", 100, 0.9)


class InMemoryEmbedder(Embedder):
    """Deterministic hash-based embedder — produces reproducible vectors."""

    DIMS = 16

    def embed(self, text: str) -> list[float]:
        """Generate a unit-normalised hash-based vector from *text*.

        Two identical strings always produce identical vectors; deliberately
        different strings produce vectors with lower cosine similarity.
        """
        import hashlib
        import math

        h = hashlib.sha256(text.encode()).digest()
        # Build a raw float vector from byte pairs
        raw: list[float] = []
        for i in range(self.DIMS):
            byte_val = h[i % len(h)]
            raw.append(float(byte_val) - 128.0)

        # L2-normalise so cosine similarity == dot product
        magnitude = math.sqrt(sum(v * v for v in raw))
        if magnitude == 0:
            return [0.0] * self.DIMS
        return [v / magnitude for v in raw]


class InMemoryDedupStore(DedupStore):
    """In-memory dedup store using cosine similarity."""

    def __init__(self) -> None:
        # List of (signal_id, embedding, stored_at)
        self._entries: list[tuple[str, list[float], datetime]] = []
        self._dropped: set[str] = set()

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        import math

        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def find_similar(
        self, embedding: list[float], within_hours: float, threshold: float
    ) -> Optional[str]:
        now = datetime.now(tz=timezone.utc)
        cutoff_seconds = within_hours * 3600
        for signal_id, stored_emb, stored_at in self._entries:
            # Ensure both are tz-aware for comparison
            if stored_at.tzinfo is None:
                stored_at_aware = stored_at.replace(tzinfo=timezone.utc)
            else:
                stored_at_aware = stored_at
            age_seconds = (now - stored_at_aware).total_seconds()
            if age_seconds > cutoff_seconds:
                continue
            sim = self._cosine(embedding, stored_emb)
            if sim >= threshold:
                return signal_id
        return None

    def store(
        self, signal_id: str, embedding: list[float], timestamp: datetime
    ) -> None:
        self._entries.append((signal_id, embedding, timestamp))

    def mark_dropped(self, signal_id: str) -> None:
        self._dropped.add(signal_id)

    @property
    def dropped_ids(self) -> set[str]:
        return set(self._dropped)


class InMemoryHumanReviewQueue(HumanReviewQueue):
    """In-memory human review queue."""

    def __init__(self) -> None:
        # Each entry: (signal, reason, enqueued_at)
        self._items: list[tuple[Signal, str, datetime]] = []

    def add(self, signal: Signal, reason: str) -> None:
        self._items.append((signal, reason, datetime.now(tz=timezone.utc)))

    def depth(self) -> int:
        return len(self._items)

    def age_p90_seconds(self) -> float:
        if not self._items:
            return 0.0
        now = datetime.now(tz=timezone.utc)
        ages = sorted(
            (now - item[2]).total_seconds() for item in self._items
        )
        idx = int(len(ages) * 0.9)
        idx = min(idx, len(ages) - 1)
        return ages[idx]

    @property
    def items(self) -> list[tuple[Signal, str, datetime]]:
        return list(self._items)


class InMemoryMetricsCollector(MetricsCollector):
    """In-memory metrics collector for test assertions."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        # Store all increments with tags for detailed assertions
        self._increments: list[tuple[str, int, Optional[dict]]] = []

    def increment(
        self, metric: str, value: int = 1, tags: Optional[dict] = None
    ) -> None:
        self._counters[metric] = self._counters.get(metric, 0) + value
        self._increments.append((metric, value, tags))

    def gauge(
        self, metric: str, value: float, tags: Optional[dict] = None
    ) -> None:
        self._gauges[metric] = value

    def get_count(self, metric: str) -> int:
        return self._counters.get(metric, 0)

    def get_gauge(self, metric: str) -> float:
        return self._gauges.get(metric, 0.0)

    def get_increments_for(
        self, metric: str
    ) -> list[tuple[str, int, Optional[dict]]]:
        return [inc for inc in self._increments if inc[0] == metric]


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


class SignalClassifier:
    """Processes signals from input_queue through a 4-step classification pipeline."""

    def __init__(
        self,
        input_queue: MessageQueue,
        output_queue: MessageQueue,
        llm_client: LLMClient,
        embedder: Embedder,
        dedup_store: DedupStore,
        human_queue: HumanReviewQueue,
        metrics: MetricsCollector,
        config: ClassifierConfig,
    ) -> None:
        self._input = input_queue
        self._output = output_queue
        self._llm = llm_client
        self._embedder = embedder
        self._dedup = dedup_store
        self._human = human_queue
        self._metrics = metrics
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_batch(self) -> int:
        """Pull up to config.batch_size signals and run the full pipeline.

        Returns the number of signals that were processed (pulled from queue).
        """
        signals = self._input.dequeue(batch_size=self._config.batch_size)
        for signal in signals:
            self._process_one(signal)
        return len(signals)

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    def pre_filter(self, signal: Signal) -> tuple[bool, Optional[str]]:
        """Step 1 — Rule-based pre-filter.

        Returns:
            (should_drop, rule_name)
            If should_drop is True and rule_name is None, the signal was
            parked in the human queue (stale) rather than silently dropped.
        """
        # Rule: empty or too-short content
        if not signal.raw_content or len(signal.raw_content.strip()) < 10:
            return True, "empty_content"

        # Rule: Slack spam bots
        if (
            signal.source == "slack"
            and signal.type == "bot_message"
            and signal.originator.name in self._config.spam_bot_names
        ):
            return True, "spam_bot"

        # Rule: GUS task noise — drop task signals unless they are explicitly
        # actionable types.
        from feed_layer.shared.signal import GusMetadata

        if (
            signal.source == "gus"
            and isinstance(signal.metadata, GusMetadata)
            and signal.metadata.gus_item_type == "task"
            and signal.type not in ("task.blocked", "manual_label")
        ):
            return True, "gus_task_noise"

        # Rule: stale signal (> 48 h old) → park in human queue
        now = datetime.now(tz=timezone.utc)
        ingested = signal.ingested_at
        if ingested.tzinfo is None:
            ingested = ingested.replace(tzinfo=timezone.utc)
        age_hours = (now - ingested).total_seconds() / 3600
        if age_hours > 48:
            signal.classification = Classification(
                result="uncertain",
                prompt_version=self._config.prompt_version,
                classified_at=datetime.now(tz=timezone.utc),
                pre_filter_hit="stale",
            )
            self._human.add(signal, reason="stale")
            return True, None  # parked, not silently dropped

        return False, None

    def dedup_check(self, signal: Signal) -> tuple[bool, Optional[str]]:
        """Step 2 — Semantic deduplication.

        Returns:
            (is_duplicate, canonical_id)
        """
        embedding = self._embedder.embed(signal.raw_content)

        canonical_id = self._dedup.find_similar(
            embedding, within_hours=2, threshold=0.92
        )

        if canonical_id is not None:
            # Store for audit but do not forward
            self._dedup.store(
                signal.id, embedding, datetime.now(tz=timezone.utc)
            )
            return True, canonical_id

        # Not a duplicate — store for future dedup checks
        self._dedup.store(
            signal.id, embedding, datetime.now(tz=timezone.utc)
        )
        return False, None

    def llm_classify(
        self, signal: Signal
    ) -> tuple[Optional[str], int, Optional[float]]:
        """Step 3 — LLM classification with retry and budget guard.

        Returns:
            (result, tokens_used, confidence)
            result is None when the signal was parked (budget exhausted or
            LLM permanently unavailable).
        """
        # Budget guard
        if self._config.daily_token_budget_remaining() == 0:
            # Park back in input queue with a delay — not dropped
            self._input.enqueue(signal)
            return None, 0, None

        delays = [1, 2, 4]
        last_exc: Optional[Exception] = None

        # Truncate a *copy* of the signal's raw_content before handing it to
        # the LLM (README: raw content truncated to ~2,000 tokens). The
        # stored signal object is never mutated — only the copy passed to
        # the LLM client is shortened.
        llm_signal = signal
        max_chars = self._config.max_llm_content_chars
        if signal.raw_content and len(signal.raw_content) > max_chars:
            llm_signal = replace(
                signal, raw_content=signal.raw_content[:max_chars]
            )

        for attempt, delay in enumerate(delays):
            try:
                result, tokens_used, confidence = self._llm.classify(
                    llm_signal, prompt_version=self._config.prompt_version
                )
                # Track token usage
                self._metrics.increment(LLM_TOKENS_USED, tokens_used)
                self._config.consume_tokens(tokens_used)
                return result, tokens_used, confidence
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < len(delays) - 1:
                    time.sleep(delay)

        # All retries exhausted
        self._human.add(signal, reason="llm_unavailable")
        self._metrics.increment(LLM_FALLBACK_COUNT)
        return None, 0, None

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _process_one(self, signal: Signal) -> None:
        """Run the full 4-step pipeline for a single signal."""
        # Emit ingested metric by source
        self._metrics.increment(
            SIGNALS_INGESTED, tags={"source": signal.source}
        )

        # Step 1: pre-filter
        should_drop, rule = self.pre_filter(signal)
        if should_drop:
            if rule is not None:
                # Silently dropped — emit metric and record the rule that
                # fired so the drop reason is auditable on the signal itself.
                self._metrics.increment(
                    SIGNALS_PRE_FILTER_DROPPED, tags={"rule": rule}
                )
                signal.classification = Classification(
                    result="irrelevant",
                    prompt_version=self._config.prompt_version,
                    classified_at=datetime.now(tz=timezone.utc),
                    pre_filter_hit=rule,
                )
            # If rule is None the signal was parked (stale) — pre_filter()
            # already attached an "uncertain" classification with
            # pre_filter_hit="stale" above.
            self._input.ack(signal.id)
            return

        # Step 2: dedup
        is_dup, canonical_id = self.dedup_check(signal)
        if is_dup:
            self._metrics.increment(SIGNALS_DEDUPLICATED)
            # Attach dedup info to classification for audit purposes
            signal.classification = Classification(
                result="irrelevant",
                prompt_version=self._config.prompt_version,
                classified_at=datetime.now(tz=timezone.utc),
                duplicate_of=canonical_id,
            )
            self._input.ack(signal.id)
            return

        # Step 3: LLM classify
        result, tokens_used, confidence = self.llm_classify(signal)
        if result is None:
            # Parked — either budget exhausted (re-queued) or llm_unavailable
            # (in human queue). Either way, ack the current dequeue.
            self._input.ack(signal.id)
            return

        # Attach classification to the signal
        signal.classification = Classification(
            result=result,  # type: ignore[arg-type]
            prompt_version=self._config.prompt_version,
            classified_at=datetime.now(tz=timezone.utc),
            confidence=confidence,
        )

        # Step 4: route
        self._route(signal, result)
        self._input.ack(signal.id)

        # Emit queue-level gauges after routing
        self._metrics.gauge(UNCERTAIN_QUEUE_DEPTH, self._human.depth())
        self._metrics.gauge(
            UNCERTAIN_QUEUE_AGE_P90, self._human.age_p90_seconds()
        )

    def _route(self, signal: Signal, result: str) -> None:
        """Step 4 — Route the classified signal to the correct destination."""
        if result == "relevant":
            self._output.enqueue(signal)
            self._metrics.increment(SIGNALS_CLASSIFIED_RELEVANT)
        elif result == "irrelevant":
            self._dedup.mark_dropped(signal.id)
            self._metrics.increment(SIGNALS_CLASSIFIED_IRRELEVANT)
        else:  # "uncertain"
            self._human.add(signal, reason="uncertain")
            self._metrics.increment(SIGNALS_CLASSIFIED_UNCERTAIN)

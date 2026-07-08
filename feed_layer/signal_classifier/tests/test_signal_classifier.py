"""Tests for the Signal Classifier — all 22 cases from the spec.

All tests use in-memory implementations only; no real LLM or embedding
API calls are made.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import pytest

from feed_layer.shared import (
    Classification,
    GusMetadata,
    InMemoryQueue,
    Originator,
    Signal,
    SlackMetadata,
)
from feed_layer.signal_classifier.classifier import (
    ClassifierConfig,
    InMemoryDedupStore,
    InMemoryEmbedder,
    InMemoryHumanReviewQueue,
    InMemoryLLMClient,
    InMemoryMetricsCollector,
    InMemoryTokenBudgetStore,
    LLMClient,
    SignalClassifier,
    TokenBudgetStore,
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
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def make_signal(
    source: str = "slack",
    signal_type: str = "manual_reaction",
    raw_content: str = "This is a valid signal with enough content.",
    ingested_at: Optional[datetime] = None,
    originator_name: str = "alice",
    metadata: Optional[object] = None,
) -> Signal:
    """Factory for test signals."""
    if ingested_at is None:
        ingested_at = _now()
    if metadata is None:
        metadata = SlackMetadata(channel_id="C123", message_ts="12345.0")
    return Signal(
        source=source,  # type: ignore[arg-type]
        type=signal_type,  # type: ignore[arg-type]
        raw_content=raw_content,
        timestamp=_now(),
        ingested_at=ingested_at,
        originator=Originator(id="U1", name=originator_name),
        metadata=metadata,
    )


def make_gus_signal(
    signal_type: str = "task.created",
    gus_item_type: str = "task",
    raw_content: str = "GUS task signal content is long enough here.",
) -> Signal:
    return Signal(
        source="gus",  # type: ignore[arg-type]
        type=signal_type,  # type: ignore[arg-type]
        raw_content=raw_content,
        timestamp=_now(),
        ingested_at=_now(),
        originator=Originator(id="U2", name="gus_bot"),
        metadata=GusMetadata(
            gus_item_id="W-123",
            gus_item_type=gus_item_type,  # type: ignore[arg-type]
            gus_item_url="https://gus.example.com/W-123",
            team="phoenix",
        ),
    )


def make_classifier(
    llm_responses: Optional[list] = None,
    batch_size: int = 10,
    daily_token_budget: int = 100_000,
    spam_bot_names: Optional[list[str]] = None,
) -> tuple[
    SignalClassifier,
    InMemoryQueue,
    InMemoryQueue,
    InMemoryHumanReviewQueue,
    InMemoryMetricsCollector,
    ClassifierConfig,
]:
    input_q: InMemoryQueue = InMemoryQueue()
    output_q: InMemoryQueue = InMemoryQueue()
    human_q = InMemoryHumanReviewQueue()
    metrics = InMemoryMetricsCollector()
    config = ClassifierConfig(
        batch_size=batch_size,
        daily_token_budget=daily_token_budget,
        spam_bot_names=spam_bot_names if spam_bot_names is not None else ["giphy", "polly"],
    )
    llm = InMemoryLLMClient(responses=llm_responses or [("relevant", 100, 0.9)])
    embedder = InMemoryEmbedder()
    dedup = InMemoryDedupStore()

    classifier = SignalClassifier(
        input_queue=input_q,
        output_queue=output_q,
        llm_client=llm,
        embedder=embedder,
        dedup_store=dedup,
        human_queue=human_q,
        metrics=metrics,
        config=config,
    )
    return classifier, input_q, output_q, human_q, metrics, config


# ---------------------------------------------------------------------------
# Step 1: Pre-filter tests
# ---------------------------------------------------------------------------


class TestPreFilter:
    def test_empty_content_dropped(self):
        """Test 1: Signal with empty raw_content is dropped, rule=empty_content."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_signal(raw_content="")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert human_q.depth() == 0
        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 1
        drops = metrics.get_increments_for(SIGNALS_PRE_FILTER_DROPPED)
        assert drops[0][2] == {"rule": "empty_content"}

    def test_short_content_dropped(self):
        """Test 2: Signal with raw_content shorter than 10 chars (stripped) is dropped."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_signal(raw_content="hi")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 1

    def test_whitespace_only_short_dropped(self):
        """Edge: whitespace-padded short content is still dropped after strip."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_signal(raw_content="   hi   ")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 1

    def test_slack_spam_bot_dropped(self):
        """Test 3: Slack bot_message from 'giphy' is dropped, rule=spam_bot."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_signal(
            source="slack",
            signal_type="bot_message",
            originator_name="giphy",
        )
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        drops = metrics.get_increments_for(SIGNALS_PRE_FILTER_DROPPED)
        assert any(d[2] == {"rule": "spam_bot"} for d in drops)

    def test_slack_unknown_bot_not_dropped(self):
        """Test 4: Slack bot_message from 'alertmanager' (not in spam list) is NOT dropped."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        sig = make_signal(
            source="slack",
            signal_type="bot_message",
            originator_name="alertmanager",
        )
        input_q.enqueue(sig)
        classifier.process_batch()

        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 0
        assert output_q.depth() == 1

    def test_gus_task_created_dropped(self):
        """Test 5: GUS signal type=task.created is dropped, rule=gus_task_noise."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_gus_signal(signal_type="task.created")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        drops = metrics.get_increments_for(SIGNALS_PRE_FILTER_DROPPED)
        assert any(d[2] == {"rule": "gus_task_noise"} for d in drops)

    def test_gus_task_blocked_not_dropped(self):
        """Test 6: GUS signal type=task.blocked is NOT dropped by pre-filter."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        sig = make_gus_signal(signal_type="task.blocked")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 0

    def test_stale_signal_parked_in_human_queue(self):
        """Test 7: Signal ingested 49 hours ago is parked in human_queue with reason=stale."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        ingested_at = _now() - timedelta(hours=49)
        sig = make_signal(ingested_at=ingested_at)
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        # Parked in human queue, not silently dropped
        assert human_q.depth() == 1
        reason = human_q.items[0][1]
        assert reason == "stale"
        # No drop metric for stale (it's parked, not dropped)
        assert metrics.get_count(SIGNALS_PRE_FILTER_DROPPED) == 0


# ---------------------------------------------------------------------------
# Step 2: Semantic dedup tests
# ---------------------------------------------------------------------------


class TestSemanticDedup:
    def test_near_duplicate_within_window_is_deduplicated(self):
        """Test 8: Two identical signals within 2h → second is marked duplicate_of first."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9), ("relevant", 100, 0.9)]
        )
        content = "Identical content that is long enough for dedup check"
        sig1 = make_signal(raw_content=content)
        sig2 = make_signal(raw_content=content)

        input_q.enqueue(sig1)
        classifier.process_batch()
        # sig1 should be forwarded (first seen)
        assert output_q.depth() == 1

        input_q.enqueue(sig2)
        classifier.process_batch()
        # sig2 is a duplicate — should NOT be forwarded
        assert output_q.depth() == 1
        assert metrics.get_count(SIGNALS_DEDUPLICATED) == 1

    def test_near_duplicate_marked_with_canonical_id(self):
        """Test 8 (cont.): Duplicate signal has classification.duplicate_of = first signal's id."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        content = "Same content used for canonical id dedup check test case here"
        sig1 = make_signal(raw_content=content)
        sig2 = make_signal(raw_content=content)

        input_q.enqueue(sig1)
        classifier.process_batch()

        input_q.enqueue(sig2)
        classifier.process_batch()

        # sig2 should have classification.duplicate_of == sig1.id
        assert sig2.classification is not None
        assert sig2.classification.duplicate_of == sig1.id

    def test_similar_signals_outside_window_not_deduplicated(self):
        """Test 9: Identical content but more than 2 hours apart → NOT deduplicated."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9), ("relevant", 100, 0.9)]
        )
        content = "Identical signal content outside the dedup window here"
        sig1 = make_signal(raw_content=content)
        sig2 = make_signal(raw_content=content)

        # Manually pre-populate dedup store with sig1's embedding at a time
        # that is > 2h in the past so the window check fails.
        old_time = _now() - timedelta(hours=3)
        embedding = InMemoryEmbedder().embed(content)
        classifier._dedup.store(sig1.id, embedding, old_time)

        input_q.enqueue(sig2)
        classifier.process_batch()

        # sig2 should NOT be deduplicated — different time window
        assert metrics.get_count(SIGNALS_DEDUPLICATED) == 0
        assert output_q.depth() == 1

    def test_dissimilar_signals_not_deduplicated(self):
        """Test 10: Two signals with cosine similarity < 0.92 → both forwarded."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9), ("relevant", 100, 0.9)]
        )
        sig1 = make_signal(raw_content="Signal about deployment pipeline issue alpha")
        sig2 = make_signal(raw_content="Totally different topic about marketing campaign z")

        input_q.enqueue(sig1)
        classifier.process_batch()
        input_q.enqueue(sig2)
        classifier.process_batch()

        assert metrics.get_count(SIGNALS_DEDUPLICATED) == 0
        assert output_q.depth() == 2

    def test_dedup_metric_incremented(self):
        """Test 11: Deduplicated signal increments signals.deduplicated metric."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        content = "Exact same content so dedup metric is triggered and incremented"
        sig1 = make_signal(raw_content=content)
        sig2 = make_signal(raw_content=content)

        input_q.enqueue(sig1)
        classifier.process_batch()
        input_q.enqueue(sig2)
        classifier.process_batch()

        assert metrics.get_count(SIGNALS_DEDUPLICATED) == 1


# ---------------------------------------------------------------------------
# Step 3: LLM classification tests
# ---------------------------------------------------------------------------


class TestLLMClassification:
    def test_relevant_forwarded_to_output_queue(self):
        """Test 12: LLM returns 'relevant' → signal forwarded to output_queue."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        sig = make_signal()
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 1
        assert metrics.get_count(SIGNALS_CLASSIFIED_RELEVANT) == 1

    def test_irrelevant_dropped_with_metric(self):
        """Test 13: LLM returns 'irrelevant' → signal dropped, metric incremented."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("irrelevant", 50, 0.95)]
        )
        sig = make_signal()
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert human_q.depth() == 0
        assert metrics.get_count(SIGNALS_CLASSIFIED_IRRELEVANT) == 1

    def test_uncertain_sent_to_human_queue(self):
        """Test 14: LLM returns 'uncertain' → signal sent to human_queue."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("uncertain", 75, 0.4)]
        )
        sig = make_signal()
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert human_q.depth() == 1
        reason = human_q.items[0][1]
        assert reason == "uncertain"
        assert metrics.get_count(SIGNALS_CLASSIFIED_UNCERTAIN) == 1

    def test_llm_retry_succeeds_on_third_attempt(self):
        """Test 15: LLM raises on first 2 calls, succeeds on 3rd → signal classified."""
        # First two entries are exceptions, third is a valid response
        responses: list = [
            RuntimeError("5xx error"),
            RuntimeError("rate limit"),
            ("relevant", 100, 0.9),
        ]

        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        # Replace LLM with the one that has exception responses
        from feed_layer.signal_classifier.classifier import InMemoryLLMClient

        class _ExceptionLLMClient(InMemoryLLMClient):
            def __init__(self):
                self._call_count = 0
                self._responses = responses

            def classify(self, signal, prompt_version):
                resp = self._responses[self._call_count]
                self._call_count += 1
                if isinstance(resp, Exception):
                    raise resp
                return resp

        classifier._llm = _ExceptionLLMClient()

        with patch("time.sleep"):  # don't actually sleep in tests
            sig = make_signal()
            input_q.enqueue(sig)
            classifier.process_batch()

        assert output_q.depth() == 1
        assert human_q.depth() == 0

    def test_llm_all_retries_fail_parks_in_human_queue(self):
        """Test 16: LLM raises on all 3 retries → signal parked in human_queue."""

        class _AlwaysFailLLM(InMemoryLLMClient):
            def classify(self, signal, prompt_version):
                raise RuntimeError("persistent LLM failure")

        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        classifier._llm = _AlwaysFailLLM()

        with patch("time.sleep"):
            sig = make_signal()
            input_q.enqueue(sig)
            classifier.process_batch()

        assert output_q.depth() == 0
        assert human_q.depth() == 1
        assert human_q.items[0][1] == "llm_unavailable"
        assert metrics.get_count(LLM_FALLBACK_COUNT) == 1

    def test_token_budget_exhausted_parks_signal_back(self):
        """Test 17: Daily token budget exhausted → signal held, not dropped."""
        classifier, input_q, output_q, human_q, metrics, config = make_classifier(
            daily_token_budget=0
        )
        # Exhaust budget by setting _tokens_used_today to equal the budget
        config._tokens_used_today = config.daily_token_budget

        sig = make_signal()
        input_q.enqueue(sig)
        classifier.process_batch()

        # Signal must NOT be dropped or sent to human_queue; it should be
        # re-enqueued back into input_queue
        assert output_q.depth() == 0
        assert human_q.depth() == 0
        # Signal is re-enqueued — input_queue now has it again
        assert input_q.depth() == 1


# ---------------------------------------------------------------------------
# Step 4: Routing and metrics tests
# ---------------------------------------------------------------------------


class TestRoutingAndMetrics:
    def test_batch_mixed_classification(self):
        """Test 18: 5 relevant, 3 irrelevant, 2 uncertain → correct routing and metrics."""
        responses = (
            [("relevant", 100, 0.9)] * 5
            + [("irrelevant", 50, 0.95)] * 3
            + [("uncertain", 75, 0.4)] * 2
        )
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=responses, batch_size=10
        )

        # Use varied content to avoid dedup triggering
        contents = [
            f"Unique signal content number {i} with enough length for classifier"
            for i in range(10)
        ]
        signals = [make_signal(raw_content=c) for c in contents]
        for s in signals:
            input_q.enqueue(s)

        classifier.process_batch()

        assert output_q.depth() == 5
        assert human_q.depth() == 2
        assert metrics.get_count(SIGNALS_CLASSIFIED_RELEVANT) == 5
        assert metrics.get_count(SIGNALS_CLASSIFIED_IRRELEVANT) == 3
        assert metrics.get_count(SIGNALS_CLASSIFIED_UNCERTAIN) == 2

    def test_signals_ingested_metric_incremented_by_source(self):
        """Test 19: signals.ingested metric is incremented with source tag."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)] * 3
        )
        sig_slack = make_signal(source="slack")
        sig_gus = make_gus_signal(signal_type="task.blocked")
        sig_slack2 = make_signal(source="slack", raw_content="Another slack signal here!")

        input_q.enqueue(sig_slack)
        input_q.enqueue(sig_gus)
        input_q.enqueue(sig_slack2)
        classifier.process_batch()

        increments = metrics.get_increments_for(SIGNALS_INGESTED)
        sources_seen = [inc[2]["source"] for inc in increments if inc[2]]
        assert sources_seen.count("slack") == 2
        assert sources_seen.count("gus") == 1

    def test_llm_tokens_used_cumulative_across_batch(self):
        """Test 20: classifier.llm_tokens_used tracks cumulative tokens across batch."""
        responses = [
            ("relevant", 100, 0.9),
            ("relevant", 200, 0.9),
            ("irrelevant", 150, 0.95),
        ]
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=responses, batch_size=3
        )
        contents = [
            "First signal content unique alpha beta gamma delta epsilon",
            "Second signal content unique foxtrot golf hotel india juliet",
            "Third signal content unique kilo lima mike november oscar",
        ]
        for c in contents:
            input_q.enqueue(make_signal(raw_content=c))

        classifier.process_batch()

        assert metrics.get_count(LLM_TOKENS_USED) == 100 + 200 + 150


# ---------------------------------------------------------------------------
# Schema correctness tests
# ---------------------------------------------------------------------------


class TestSchemaCorrectness:
    def test_forwarded_signal_has_correct_classification_fields(self):
        """Test 21: Forwarded signal has classification.result, prompt_version, classified_at."""
        config = ClassifierConfig(prompt_version="v2")
        input_q: InMemoryQueue = InMemoryQueue()
        output_q: InMemoryQueue = InMemoryQueue()
        human_q = InMemoryHumanReviewQueue()
        metrics = InMemoryMetricsCollector()

        classifier = SignalClassifier(
            input_queue=input_q,
            output_queue=output_q,
            llm_client=InMemoryLLMClient(responses=[("relevant", 100, 0.85)]),
            embedder=InMemoryEmbedder(),
            dedup_store=InMemoryDedupStore(),
            human_queue=human_q,
            metrics=metrics,
            config=config,
        )

        sig = make_signal()
        input_q.enqueue(sig)
        before = _now()
        classifier.process_batch()
        after = _now()

        # Retrieve the forwarded signal
        forwarded = output_q.dequeue(batch_size=1)
        assert len(forwarded) == 1
        s = forwarded[0]
        assert s.classification is not None
        assert s.classification.result == "relevant"
        assert s.classification.prompt_version == "v2"
        assert s.classification.classified_at >= before
        assert s.classification.classified_at <= after

    def test_signal_id_unchanged(self):
        """Test 22: Signal id is not modified by the classifier."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier(
            llm_responses=[("relevant", 100, 0.9)]
        )
        sig = make_signal()
        original_id = sig.id

        input_q.enqueue(sig)
        classifier.process_batch()

        forwarded = output_q.dequeue(batch_size=1)
        assert len(forwarded) == 1
        assert forwarded[0].id == original_id


# ---------------------------------------------------------------------------
# InMemoryEmbedder determinism test (infrastructure validation)
# ---------------------------------------------------------------------------


class TestInMemoryEmbedder:
    def test_same_text_produces_same_vector(self):
        """Identical text → identical embedding (deterministic)."""
        emb = InMemoryEmbedder()
        v1 = emb.embed("hello world")
        v2 = emb.embed("hello world")
        assert v1 == v2

    def test_different_text_produces_different_vector(self):
        """Different text → different embedding."""
        emb = InMemoryEmbedder()
        v1 = emb.embed("hello world")
        v2 = emb.embed("completely different content xyz abc 123")
        assert v1 != v2

    def test_embedding_is_unit_normalised(self):
        """Embedding should be L2-normalised (magnitude ≈ 1.0)."""
        emb = InMemoryEmbedder()
        v = emb.embed("some text to embed here")
        magnitude = math.sqrt(sum(x * x for x in v))
        assert abs(magnitude - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# GAP 1: pre_filter_hit schema field population
# ---------------------------------------------------------------------------


class TestPreFilterHitRecorded:
    def test_spam_bot_drop_records_rule_name_in_classification(self):
        """A signal dropped by the giphy spam rule has
        classification.pre_filter_hit == 'spam_bot' and result == 'irrelevant'.
        """
        classifier, input_q, output_q, human_q, metrics, config = make_classifier()
        sig = make_signal(
            source="slack",
            signal_type="bot_message",
            originator_name="giphy",
        )
        input_q.enqueue(sig)
        classifier.process_batch()

        assert output_q.depth() == 0
        assert sig.classification is not None
        assert sig.classification.result == "irrelevant"
        assert sig.classification.pre_filter_hit == "spam_bot"
        assert sig.classification.prompt_version == config.prompt_version
        assert sig.classification.classified_at is not None

    def test_empty_content_drop_records_rule_name(self):
        """A signal dropped for empty content also records pre_filter_hit."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_signal(raw_content="")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert sig.classification is not None
        assert sig.classification.result == "irrelevant"
        assert sig.classification.pre_filter_hit == "empty_content"

    def test_gus_task_noise_drop_records_rule_name(self):
        """A GUS task-noise drop records pre_filter_hit == 'gus_task_noise'."""
        classifier, input_q, output_q, human_q, metrics, _ = make_classifier()
        sig = make_gus_signal(signal_type="task.created")
        input_q.enqueue(sig)
        classifier.process_batch()

        assert sig.classification is not None
        assert sig.classification.result == "irrelevant"
        assert sig.classification.pre_filter_hit == "gus_task_noise"

    def test_stale_parked_signal_records_rule_name(self):
        """A stale-parked signal (>48h old) records pre_filter_hit == 'stale'
        with result == 'uncertain' rather than a silent drop.
        """
        classifier, input_q, output_q, human_q, metrics, config = make_classifier()
        ingested_at = _now() - timedelta(hours=49)
        sig = make_signal(ingested_at=ingested_at)
        input_q.enqueue(sig)
        classifier.process_batch()

        assert human_q.depth() == 1
        assert sig.classification is not None
        assert sig.classification.result == "uncertain"
        assert sig.classification.pre_filter_hit == "stale"
        assert sig.classification.prompt_version == config.prompt_version
        assert sig.classification.classified_at is not None


# ---------------------------------------------------------------------------
# GAP 2: date-aware, injectable token budget accounting
# ---------------------------------------------------------------------------


class TestTokenBudgetAccounting:
    def test_tokens_used_accumulate_within_a_day(self):
        """Multiple LLM calls on the same day accumulate against the same
        budget key, eventually exhausting it.
        """
        responses = [
            ("relevant", 60, 0.9),
            ("relevant", 60, 0.9),
            ("relevant", 60, 0.9),
        ]
        classifier, input_q, output_q, human_q, metrics, config = make_classifier(
            llm_responses=responses, daily_token_budget=100
        )
        contents = [
            "First unique signal content for budget accumulation test one",
            "Second unique signal content for budget accumulation test two",
            "Third unique signal content for budget accumulation test three",
        ]
        input_q.enqueue(make_signal(raw_content=contents[0]))
        classifier.process_batch()
        # First call consumes 60 of 100 tokens
        assert config.daily_token_budget_remaining() == 40

        input_q.enqueue(make_signal(raw_content=contents[1]))
        classifier.process_batch()
        # Second call consumes another 60, taking usage to 120/100 — budget
        # accounting accumulates the actual usage even past the nominal cap.
        assert config.daily_token_budget_remaining() == 0

        # A third signal arrives once the budget shows zero remaining — the
        # guard now engages and the signal is parked back in the queue
        # rather than calling the LLM again.
        input_q.enqueue(make_signal(raw_content=contents[2]))
        classifier.process_batch()
        assert input_q.depth() == 1
        assert config.daily_token_budget_remaining() == 0

    def test_crossing_day_boundary_resets_remaining_budget(self):
        """An injected clock that reports a new date resets the effective
        remaining budget even though the store instance is unchanged.
        """
        store = InMemoryTokenBudgetStore()
        current_day = ["2026-07-08"]

        config = ClassifierConfig(
            daily_token_budget=100,
            token_budget_store=store,
            today=lambda: current_day[0],
        )
        input_q: InMemoryQueue = InMemoryQueue()
        output_q: InMemoryQueue = InMemoryQueue()
        human_q = InMemoryHumanReviewQueue()
        metrics = InMemoryMetricsCollector()
        classifier = SignalClassifier(
            input_queue=input_q,
            output_queue=output_q,
            llm_client=InMemoryLLMClient(
                responses=[("relevant", 100, 0.9), ("relevant", 100, 0.9)]
            ),
            embedder=InMemoryEmbedder(),
            dedup_store=InMemoryDedupStore(),
            human_queue=human_q,
            metrics=metrics,
            config=config,
        )

        input_q.enqueue(make_signal(raw_content="Day one signal content long enough here"))
        classifier.process_batch()
        # Budget for day one fully consumed
        assert config.daily_token_budget_remaining() == 0

        # A second signal on day one should be parked (budget exhausted).
        input_q.enqueue(make_signal(raw_content="Day one second signal content long enough"))
        classifier.process_batch()
        assert input_q.depth() == 1
        input_q.dequeue(batch_size=10)  # drain so it doesn't interfere below

        # Cross the day boundary via the injected clock.
        current_day[0] = "2026-07-09"
        assert config.daily_token_budget_remaining() == 100

        input_q.enqueue(make_signal(raw_content="Day two signal content long enough here"))
        classifier.process_batch()
        assert output_q.depth() == 2  # day-one signal + day-two signal forwarded

    def test_durable_store_retains_usage_across_two_classifier_instances(self):
        """A shared TokenBudgetStore persists usage even when a brand new
        SignalClassifier/ClassifierConfig pair is constructed, simulating a
        process restart against a durable backend.
        """
        shared_store = InMemoryTokenBudgetStore()
        fixed_day = "2026-07-08"

        def build():
            input_q: InMemoryQueue = InMemoryQueue()
            output_q: InMemoryQueue = InMemoryQueue()
            human_q = InMemoryHumanReviewQueue()
            metrics = InMemoryMetricsCollector()
            config = ClassifierConfig(
                daily_token_budget=150,
                token_budget_store=shared_store,
                today=lambda: fixed_day,
            )
            classifier = SignalClassifier(
                input_queue=input_q,
                output_queue=output_q,
                llm_client=InMemoryLLMClient(responses=[("relevant", 100, 0.9)]),
                embedder=InMemoryEmbedder(),
                dedup_store=InMemoryDedupStore(),
                human_queue=human_q,
                metrics=metrics,
                config=config,
            )
            return classifier, input_q, output_q, config

        classifier1, input_q1, output_q1, config1 = build()
        input_q1.enqueue(make_signal(raw_content="Instance one signal content long enough"))
        classifier1.process_batch()
        assert config1.daily_token_budget_remaining() == 50

        # Brand new classifier + config instance, but same durable store —
        # usage from "instance one" must still be reflected.
        classifier2, input_q2, output_q2, config2 = build()
        assert config2.daily_token_budget_remaining() == 50

        input_q2.enqueue(make_signal(raw_content="Instance two signal content long enough"))
        classifier2.process_batch()
        # 100 (instance one) + 100 (instance two) = 200 > 150 budget, so the
        # second call happened while remaining was 50 but consume_tokens
        # still records actual usage against the shared store.
        assert shared_store.get_tokens_used(fixed_day) == 200


# ---------------------------------------------------------------------------
# GAP 3: content truncation before LLM call
# ---------------------------------------------------------------------------


class _CapturingLLMClient(LLMClient):
    """Stub LLM client that records the raw_content it was called with."""

    def __init__(self) -> None:
        self.seen_contents: list[str] = []

    def classify(self, signal, prompt_version):
        self.seen_contents.append(signal.raw_content)
        return ("relevant", 100, 0.9)


class TestContentTruncationBeforeLLM:
    def test_over_long_content_is_truncated_for_llm(self):
        """Content longer than max_llm_content_chars is truncated in the
        text handed to the LLM, but the stored signal is left untouched.
        """
        capturing_llm = _CapturingLLMClient()
        config = ClassifierConfig(max_llm_content_chars=50)
        input_q: InMemoryQueue = InMemoryQueue()
        output_q: InMemoryQueue = InMemoryQueue()
        human_q = InMemoryHumanReviewQueue()
        metrics = InMemoryMetricsCollector()
        classifier = SignalClassifier(
            input_queue=input_q,
            output_queue=output_q,
            llm_client=capturing_llm,
            embedder=InMemoryEmbedder(),
            dedup_store=InMemoryDedupStore(),
            human_queue=human_q,
            metrics=metrics,
            config=config,
        )

        long_content = "x" * 500
        sig = make_signal(raw_content=long_content)
        input_q.enqueue(sig)
        classifier.process_batch()

        assert len(capturing_llm.seen_contents) == 1
        assert len(capturing_llm.seen_contents[0]) == 50
        assert capturing_llm.seen_contents[0] == long_content[:50]
        # The stored signal's raw_content must be unchanged.
        assert sig.raw_content == long_content
        assert len(sig.raw_content) == 500

    def test_short_content_passed_unchanged(self):
        """Content shorter than the max length is passed to the LLM as-is."""
        capturing_llm = _CapturingLLMClient()
        config = ClassifierConfig(max_llm_content_chars=2000)
        input_q: InMemoryQueue = InMemoryQueue()
        output_q: InMemoryQueue = InMemoryQueue()
        human_q = InMemoryHumanReviewQueue()
        metrics = InMemoryMetricsCollector()
        classifier = SignalClassifier(
            input_queue=input_q,
            output_queue=output_q,
            llm_client=capturing_llm,
            embedder=InMemoryEmbedder(),
            dedup_store=InMemoryDedupStore(),
            human_queue=human_q,
            metrics=metrics,
            config=config,
        )

        short_content = "This short signal content should pass through untouched."
        sig = make_signal(raw_content=short_content)
        input_q.enqueue(sig)
        classifier.process_batch()

        assert capturing_llm.seen_contents == [short_content]
        assert sig.raw_content == short_content

"""Signal Classifier package for the Aquarium feed layer."""

from .classifier import (
    ClassifierConfig,
    DedupStore,
    Embedder,
    HumanReviewQueue,
    InMemoryDedupStore,
    InMemoryEmbedder,
    InMemoryHumanReviewQueue,
    InMemoryLLMClient,
    InMemoryMetricsCollector,
    LLMClient,
    MetricsCollector,
    SignalClassifier,
)
from .metrics_names import (
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

__all__ = [
    # Main class
    "SignalClassifier",
    "ClassifierConfig",
    # Abstract interfaces
    "LLMClient",
    "Embedder",
    "DedupStore",
    "HumanReviewQueue",
    "MetricsCollector",
    # In-memory implementations
    "InMemoryLLMClient",
    "InMemoryEmbedder",
    "InMemoryDedupStore",
    "InMemoryHumanReviewQueue",
    "InMemoryMetricsCollector",
    # Metric name constants
    "SIGNALS_INGESTED",
    "SIGNALS_PRE_FILTER_DROPPED",
    "SIGNALS_DEDUPLICATED",
    "SIGNALS_CLASSIFIED_RELEVANT",
    "SIGNALS_CLASSIFIED_IRRELEVANT",
    "SIGNALS_CLASSIFIED_UNCERTAIN",
    "UNCERTAIN_QUEUE_DEPTH",
    "UNCERTAIN_QUEUE_AGE_P90",
    "LLM_TOKENS_USED",
    "LLM_FALLBACK_COUNT",
]

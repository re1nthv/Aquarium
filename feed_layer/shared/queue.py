from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from .signal import Signal


class MessageQueue(ABC):
    """Abstract interface all connectors write to and the classifier reads from."""

    @abstractmethod
    def enqueue(self, signal: Signal) -> None:
        """Enqueue a signal. Must be non-blocking — caller returns immediately."""

    @abstractmethod
    def dequeue(self, batch_size: int = 10) -> List[Signal]:
        """Pull up to batch_size signals. Returns empty list if queue is empty."""

    @abstractmethod
    def ack(self, signal_id: str) -> None:
        """Acknowledge successful processing of a signal so it is not redelivered."""

    @abstractmethod
    def nack(self, signal_id: str) -> None:
        """Negative-acknowledge — return signal to queue for redelivery."""


class InMemoryQueue(MessageQueue):
    """In-process queue for local development and testing only."""

    def __init__(self) -> None:
        self._queue: list[Signal] = []

    def enqueue(self, signal: Signal) -> None:
        self._queue.append(signal)

    def dequeue(self, batch_size: int = 10) -> List[Signal]:
        batch = self._queue[:batch_size]
        self._queue = self._queue[batch_size:]
        return batch

    def ack(self, signal_id: str) -> None:
        pass

    def nack(self, signal_id: str) -> None:
        pass

    def depth(self) -> int:
        return len(self._queue)

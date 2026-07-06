"""
E2E runner — Slack emoji reaction → print to stdout.

Starts the Flask connector on port 3000 and polls the queue every 0.5s,
printing each signal as it arrives.

Required env vars:
  SLACK_BOT_TOKEN   — bot token from your Slack app (xoxb-...)
  SLACK_SIGNING_SECRET — signing secret from your Slack app (optional but recommended)

Optional env vars:
  INTAKE_EMOJI      — emoji name without colons (default: aquarium)
  PORT              — port to listen on (default: 3000)

Usage:
  python run_e2e_slack.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import json
import logging
from datetime import datetime

# ensure the repo root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from feed_layer.shared import InMemoryQueue
from feed_layer.slack.connector import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("e2e")


def _print_signal(signal) -> None:
    """Pretty-print a Signal to stdout."""
    border = "─" * 60
    print(f"\n{border}")
    print(f"  🐟 SIGNAL RECEIVED")
    print(f"{border}")
    print(f"  id          : {signal.id}")
    print(f"  source      : {signal.source}")
    print(f"  type        : {signal.type}")
    print(f"  ingested_at : {signal.ingested_at.isoformat()}")
    print(f"  originator  : {signal.originator.name} ({signal.originator.id})")
    print(f"  channel     : {signal.metadata.channel_id}")
    print(f"  message_ts  : {signal.metadata.message_ts}")
    if signal.metadata.reaction:
        print(f"  emoji       : :{signal.metadata.reaction}:")
    print(f"  content     :")
    for line in signal.raw_content.splitlines():
        print(f"    {line}")
    print(f"{border}\n")


def _drain_loop(queue: InMemoryQueue, stop_event: threading.Event) -> None:
    """Background thread — polls queue and prints signals."""
    logger.info("Signal printer started — waiting for emoji reactions...")
    while not stop_event.is_set():
        signals = queue.dequeue(batch_size=10)
        for signal in signals:
            _print_signal(signal)
        time.sleep(0.5)


def main() -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    intake_emoji = os.environ.get("INTAKE_EMOJI", "aquarium")
    port = int(os.environ.get("PORT", "3000"))

    if not bot_token:
        print("ERROR: SLACK_BOT_TOKEN env var is required.")
        print("       export SLACK_BOT_TOKEN=xoxb-...")
        sys.exit(1)

    queue = InMemoryQueue()

    app = create_app(
        queue=queue,
        config_overrides={
            "INTAKE_EMOJI": intake_emoji,
            "SLACK_BOT_TOKEN": bot_token,
            "SLACK_SIGNING_SECRET": signing_secret,
        },
    )

    stop_event = threading.Event()
    printer_thread = threading.Thread(
        target=_drain_loop, args=(queue, stop_event), daemon=True
    )
    printer_thread.start()

    print(f"\n{'═' * 60}")
    print(f"  Aquarium — Slack E2E Runner")
    print(f"{'═' * 60}")
    print(f"  Listening on  : http://0.0.0.0:{port}")
    print(f"  Intake emoji  : :{intake_emoji}:")
    print(f"  Events URL    : http://0.0.0.0:{port}/slack/events")
    print(f"  Health check  : http://0.0.0.0:{port}/healthz")
    print(f"{'═' * 60}")
    print(f"\n  React to any Slack message with :{intake_emoji}: to see it appear here.\n")

    try:
        app.run(host="0.0.0.0", port=port, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        printer_thread.join(timeout=2)
        print("\nShutting down.")


if __name__ == "__main__":
    main()

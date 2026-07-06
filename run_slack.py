"""
Aquarium — Slack listener (Socket Mode)

Connects to Slack over a persistent WebSocket — no public URL,
no ngrok, no webhook setup required.

React to any message in the specified channel with the intake emoji
and the message will be printed to stdout.

Usage
-----
    python3 run_slack.py --channel general

Required env vars (set once, reuse forever):
    SLACK_BOT_TOKEN    — Bot token from your Slack app  (xoxb-...)
    SLACK_APP_TOKEN    — App-level token with connections:write scope (xapp-...)

Optional env vars:
    INTAKE_EMOJI       — Emoji name without colons (default: aquarium)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ─── pretty printer ──────────────────────────────────────────────────────────

def _print_signal(message_text: str, reactor: str, channel: str, emoji: str) -> None:
    border = "─" * 58
    print(f"\n{border}")
    print(f"  🐟  Signal received")
    print(f"{border}")
    print(f"  channel  : #{channel}")
    print(f"  emoji    : :{emoji}:")
    print(f"  reacted by: {reactor}")
    print(f"  message  :")
    for line in (message_text or "(empty)").splitlines():
        print(f"    {line}")
    print(f"{border}\n")


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Aquarium Slack listener")
    parser.add_argument(
        "--channel",
        required=True,
        help="Slack channel name (without #) to listen on",
    )
    parser.add_argument(
        "--emoji",
        default=os.environ.get("INTAKE_EMOJI", "aquarium"),
        help="Intake emoji name without colons (default: aquarium)",
    )
    args = parser.parse_args()

    bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
    app_token = os.environ.get("SLACK_APP_TOKEN", "")

    if not bot_token:
        print("ERROR: SLACK_BOT_TOKEN is not set.")
        print("       export SLACK_BOT_TOKEN=xoxb-...")
        sys.exit(1)
    if not app_token:
        print("ERROR: SLACK_APP_TOKEN is not set.")
        print("       export SLACK_APP_TOKEN=xapp-...")
        sys.exit(1)

    target_channel = args.channel.lstrip("#")
    intake_emoji = args.emoji

    app = App(token=bot_token)

    @app.event("reaction_added")
    def handle_reaction(event, client, logger):
        # Only care about our intake emoji
        if event.get("reaction") != intake_emoji:
            return

        item = event.get("item", {})
        channel_id = item.get("channel", "")

        # Resolve the channel name so we can filter by it
        try:
            channel_info = client.conversations_info(channel=channel_id)
            channel_name = channel_info["channel"].get("name", "")
        except Exception:
            channel_name = ""

        if channel_name != target_channel:
            return

        # Fetch the original message text
        message_text = ""
        try:
            result = client.conversations_history(
                channel=channel_id,
                latest=item.get("ts"),
                inclusive=True,
                limit=1,
            )
            messages = result.get("messages", [])
            if messages:
                message_text = messages[0].get("text", "")
        except Exception as e:
            logger.warning(f"Could not fetch message: {e}")

        # Resolve reactor display name
        reactor_id = event.get("user", "")
        reactor_name = reactor_id
        try:
            user_info = client.users_info(user=reactor_id)
            reactor_name = user_info["user"]["profile"].get("display_name") or \
                           user_info["user"]["profile"].get("real_name", reactor_id)
        except Exception:
            pass

        _print_signal(message_text, reactor_name, channel_name, intake_emoji)

    # Announce startup
    print(f"\n{'═' * 58}")
    print(f"  Aquarium — Slack Listener")
    print(f"{'═' * 58}")
    print(f"  Channel  : #{target_channel}")
    print(f"  Emoji    : :{intake_emoji}:")
    print(f"{'═' * 58}")
    print(f"\n  Listening... React to any message in #{target_channel}")
    print(f"  with :{intake_emoji}: to see it printed here.\n")
    print(f"  (Ctrl+C to stop)\n")

    SocketModeHandler(app, app_token).start()


if __name__ == "__main__":
    main()

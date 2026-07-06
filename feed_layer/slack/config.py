"""
Runtime configuration for the Slack connector.

All values can be overridden at application-startup time by passing a dict
to create_app() or by mutating these module-level constants before the app
is created (useful in tests).
"""

INTAKE_EMOJI: str = "aquarium"
"""The emoji name (without colons) that triggers the manual-reaction gate."""

MONITORED_CHANNELS: list[str] = []
"""List of Slack channel IDs whose bot messages are forwarded to the queue."""

SLACK_SIGNING_SECRET: str = ""
"""Slack signing secret used to verify request signatures."""

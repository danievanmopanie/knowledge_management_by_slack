"""Shared pytest setup for local configuration."""

import os

# Settings are instantiated at import time. Tests use harmless local placeholders.
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")

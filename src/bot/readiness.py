"""Preflight validation for Slack Socket Mode startup."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlackReadiness:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.errors

    def require_ready(self) -> None:
        if self.errors:
            joined = "\n - ".join(self.errors)
            raise RuntimeError(f"Slack configuration is not ready:\n - {joined}")


def validate_slack_readiness(settings) -> SlackReadiness:
    """Validate the minimum configuration required for a safe Slack pilot."""

    errors: list[str] = []
    warnings: list[str] = []

    token_checks = (
        ("SLACK_BOT_TOKEN", getattr(settings, "slack_bot_token", ""), "xoxb-"),
        ("SLACK_APP_TOKEN", getattr(settings, "slack_app_token", ""), "xapp-"),
        ("SLACK_SIGNING_SECRET", getattr(settings, "slack_signing_secret", ""), None),
    )
    for name, value, prefix in token_checks:
        value = (value or "").strip()
        if not value or value.startswith("your-") or "your-" in value:
            errors.append(f"{name} is missing or still contains a placeholder value.")
        elif prefix and not value.startswith(prefix):
            errors.append(f"{name} does not look like a Slack {prefix.rstrip('-')} token.")

    channel_names = (
        "channel_frontend_support",
        "channel_inventory",
        "channel_work_management",
        "channel_knowledge_uploads",
    )
    configured_channels: dict[str, str] = {}
    for name in channel_names:
        value = (getattr(settings, name, None) or "").strip()
        if not value:
            if name == "channel_inventory":
                errors.append("CHANNEL_INVENTORY is required for the inventory pilot.")
            else:
                warnings.append(f"{name.upper()} is not configured; that agent channel will be unavailable.")
            continue
        if not value.startswith("C"):
            errors.append(f"{name.upper()} should be a Slack channel ID beginning with C.")
        configured_channels[name] = value

    reverse: dict[str, list[str]] = {}
    for name, value in configured_channels.items():
        reverse.setdefault(value, []).append(name)
    for channel_id, names in reverse.items():
        if len(names) > 1:
            errors.append(
                f"Slack channel {channel_id} is assigned to multiple agents: {', '.join(names)}."
            )

    return SlackReadiness(errors=tuple(errors), warnings=tuple(warnings))

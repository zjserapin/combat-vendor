"""Provider-agnostic Claude client with a usage meter.

The Anthropic API client and the Bedrock Mantle client expose the same
`messages.create` surface and return the same `Message` type, so the swap is
one branch in `_build_client` — everything downstream is identical.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic.types import Message

from config import Config


@dataclass
class Meter:
    """Running totals for every model call. Feeds the cost line on the impact board."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    stop_reasons: dict[str, int] = field(default_factory=dict)

    def record(self, message: Message, elapsed: float) -> None:
        """Add one response's usage to the totals."""
        self.calls += 1
        self.input_tokens += message.usage.input_tokens
        self.output_tokens += message.usage.output_tokens
        self.seconds += elapsed
        reason = message.stop_reason or "unknown"
        self.stop_reasons[reason] = self.stop_reasons.get(reason, 0) + 1


class LLMClient:
    """One object per run: holds the SDK client, the model choice, and the meter."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.model = config.model_id
        self.meter = Meter()
        self._client = _build_client(config)

    def create(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16000,
    ) -> Message:
        """Send one request and meter it. Returns the raw SDK Message."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": {"effort": self.config.llm_effort},
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        message = self._client.messages.create(**kwargs)
        self.meter.record(message, time.monotonic() - started)
        return message

    def ping(self) -> str:
        """Cheapest possible round trip, used by `main.py doctor --llm`."""
        message = self.create(
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
            max_tokens=1024,
        )
        return next((b.text for b in message.content if b.type == "text"), "").strip()


def _build_client(config: Config) -> anthropic.Anthropic | anthropic.AnthropicBedrockMantle:
    if config.llm_provider == "bedrock":
        # Uses the standard AWS credential chain (SSO profile, env vars, instance role).
        return anthropic.AnthropicBedrockMantle(
            aws_region=config.aws_region,
            aws_profile=config.aws_profile or None,
        )
    return anthropic.Anthropic(api_key=config.anthropic_api_key)

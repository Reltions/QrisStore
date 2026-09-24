"""One structured Claude call: prompt in, validated Pydantic object out."""

from __future__ import annotations

import os
import threading
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from ..agent import DEFAULT_MODEL, FALLBACK_BETA, FALLBACK_MODELS, RefusalError

T = TypeVar("T", bound=BaseModel)

MAX_TOKENS = 32_000


class IncompleteOutputError(Exception):
    """Claude hit max_tokens before finishing the structured output."""


class LLM:
    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None) -> None:
        # Workflows fan out many parallel calls; retry rate limits a little more patiently.
        self.client = client or anthropic.Anthropic(max_retries=5)
        self.model = model or os.environ.get("CHEM_AGENT_MODEL") or DEFAULT_MODEL
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self._lock = threading.Lock()

    def structured(self, *, system: str, prompt: str, schema: type[T], effort: str) -> T:
        # The JSON schema goes in output_config ourselves rather than via `output_format`: the SDK
        # would validate the text as soon as it streams in, before we can see a refusal stop reason.
        json_format = {"type": "json_schema", "schema": anthropic.transform_schema(schema.model_json_schema())}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort, "format": json_format},
        }
        if self.model in FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        # Streamed so long, high-effort steps never hit HTTP timeouts.
        with self.client.beta.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
        self._count(message.usage)
        if message.stop_reason == "refusal":
            raise RefusalError("Claude declined this step.")
        if message.stop_reason == "max_tokens":
            raise IncompleteOutputError(f"{schema.__name__} output was cut off at {MAX_TOKENS} tokens.")
        # If a fallback model took over mid-answer, only the text after the switch is its output.
        parts: list[str] = []
        for block in message.content:
            if block.type == "fallback":
                parts = []
            elif block.type == "text":
                parts.append(block.text)
        text = "".join(parts)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise IncompleteOutputError(f"Response did not match {schema.__name__}: {exc}") from exc

    def _count(self, usage: Any) -> None:
        with self._lock:
            for key in self.usage:
                self.usage[key] += getattr(usage, key, 0) or 0

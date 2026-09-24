"""The research agent: a streamed Claude tool-use loop over the chemistry tools."""

from __future__ import annotations

import datetime as dt
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import anthropic
import requests

from .prompts import SYSTEM_PROMPT
from .tools import Tool, ToolContext, ToolError, available_tools, validate

DEFAULT_MODEL = "claude-opus-5"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 64_000

# Models documented to accept `fallbacks: "default"`: if a safety classifier declines a
# (usually benign) request, the API re-runs it on Anthropic's recommended fallback model.
FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 8},
]


class AgentEvents:
    """Hooks for showing the agent's progress. The default implementation shows nothing."""

    def on_text(self, delta: str) -> None: ...
    def on_thinking(self, delta: str) -> None: ...
    def on_tool_call(self, name: str, args: dict[str, Any]) -> None: ...
    def on_tool_result(self, name: str, ok: bool, content: str) -> None: ...
    def on_notice(self, message: str) -> None: ...


class RefusalError(Exception):
    """The model (and any fallback model) declined the request."""


class ResearchAgent:
    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        effort: str = "high",
        web: bool = True,
        output_dir: str | Path = "reports",
        max_steps: int = 40,
        show_thinking: bool = False,
        events: AgentEvents | None = None,
    ) -> None:
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {EFFORT_LEVELS}")
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("CHEM_AGENT_MODEL") or DEFAULT_MODEL
        self.effort = effort
        self.max_steps = max_steps
        self.show_thinking = show_thinking
        self.events = events or AgentEvents()
        self.ctx = ToolContext(output_dir=Path(output_dir))
        self.tools: dict[str, Tool] = {t.name: t for t in available_tools()}
        self.tool_params = [t.to_param() for t in self.tools.values()] + (SERVER_TOOLS if web else [])
        self.system = f"{SYSTEM_PROMPT}\nToday's date is {dt.date.today().isoformat()}."
        self.messages: list[dict[str, Any]] = []
        self.usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    def reset(self) -> None:
        self.messages = []

    def ask(self, question: str) -> str:
        """Run one research turn: loop through tool calls until Claude gives its final answer.

        If the turn fails (API error, Ctrl+C), the conversation is rolled back to before the
        question so it stays valid for the next one.
        """
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": question})
        try:
            return self._loop()
        except BaseException:
            del self.messages[checkpoint:]
            raise

    def _loop(self) -> str:
        for _ in range(self.max_steps):
            response = self._request()

            if response.stop_reason == "refusal":
                # ask() rolls the declined exchange back, so the conversation can continue.
                details = getattr(response, "stop_details", None)
                category = getattr(details, "category", None)
                raise RefusalError(f"The request was declined{f' ({category})' if category else ''}. Try rephrasing it.")

            if not response.content:
                return ""
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue  # a long server-side web search paused; re-sending resumes it

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    self.events.on_notice("Answer hit the output limit and may be cut off.")
                return _final_text(response)

            truncated = response.stop_reason == "max_tokens"
            self.messages.append({"role": "user", "content": self._run_tools(tool_uses, truncated)})

        self.events.on_notice(f"Stopped after {self.max_steps} steps. Ask a follow-up to continue.")
        return ""

    def _request(self) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": self.system,
            "tools": self.tool_params,
            "messages": self.messages,
            "thinking": {"type": "adaptive", "display": "summarized" if self.show_thinking else "omitted"},
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if self.model in FALLBACK_MODELS:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        attempts = 3
        for attempt in range(attempts):
            try:
                with self.client.beta.messages.stream(**kwargs) as stream:
                    for event in stream:
                        self._dispatch(event)
                    response = stream.get_final_message()
                break
            except ValueError:
                # An eagerly streamed tool input was not parseable JSON; there is no complete
                # tool_use block to answer, so re-issue the request.
                if attempt == attempts - 1:
                    raise
                self.events.on_notice("Received a malformed tool call; retrying.")
        self._count(response.usage)
        return response

    def _dispatch(self, event: Any) -> None:
        if event.type == "text":
            self.events.on_text(event.text)
        elif event.type == "thinking":
            self.events.on_thinking(event.thinking)
        elif event.type == "content_block_stop":
            block = event.content_block
            if block.type == "server_tool_use":
                self.events.on_tool_call(block.name, dict(block.input or {}))
            elif block.type == "fallback":
                src = getattr(getattr(block, "from_", None), "model", "the model")
                dst = getattr(getattr(block, "to", None), "model", "a fallback model")
                self.events.on_notice(f"{src} declined; {dst} continued the answer.")
            elif block.type == "text":
                self.events.on_text("\n")

    def _run_tools(self, tool_uses: list[Any], truncated: bool) -> list[dict[str, Any]]:
        def run_one(block: Any) -> dict[str, Any]:
            ok, content = False, ""
            tool = self.tools.get(block.name)
            if truncated:
                content = "Tool input was cut off at the output limit. Send a shorter input."
            elif tool is None:
                content = f"Unknown tool {block.name!r}."
            elif problems := validate(tool.input_schema, block.input):
                content = f"Invalid input ({'; '.join(problems)}). Received: {json.dumps(block.input)[:2000]}"
            else:
                self.events.on_tool_call(block.name, block.input)
                try:
                    content, ok = tool.run(block.input, self.ctx), True
                except ToolError as exc:
                    content = str(exc)
                except requests.RequestException as exc:
                    content = f"{block.name} could not reach its database: {exc}"
                except Exception as exc:  # report, don't crash the research session
                    content = f"{block.name} failed: {type(exc).__name__}: {exc}"
                self.events.on_tool_result(block.name, ok, content)
            result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            if not ok:
                result["is_error"] = True
            return result

        # Independent tool calls run concurrently; results go back together in one message.
        with ThreadPoolExecutor(max_workers=min(8, len(tool_uses))) as pool:
            return list(pool.map(run_one, tool_uses))

    def _count(self, usage: Any) -> None:
        for key in self.usage:
            self.usage[key] += getattr(usage, key, 0) or 0


def _final_text(response: Any) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()

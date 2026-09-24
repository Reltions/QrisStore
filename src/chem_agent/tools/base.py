"""Tool registry, input validation and the context passed to tools."""

from __future__ import annotations

import inspect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Tool results larger than this are cut, with an explicit marker, so one
# oversized API response cannot crowd the rest of the research out of context.
MAX_RESULT_CHARS = 60_000


class ToolError(Exception):
    """A failure the model should see and can react to (bad SMILES, no hits, ...)."""


@dataclass
class ToolContext:
    output_dir: Path = field(default_factory=lambda: Path("reports"))


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., Any]
    requires_env: str | None = None

    @property
    def available(self) -> bool:
        return self.requires_env is None or bool(os.environ.get(self.requires_env))

    def to_param(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            # Streamed requests: let large inputs (report bodies) arrive as they
            # are generated. We validate every input ourselves before running.
            "eager_input_streaming": True,
        }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        kwargs = dict(args)
        if "ctx" in inspect.signature(self.func).parameters:
            kwargs["ctx"] = ctx
        result = self.func(**kwargs)
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > MAX_RESULT_CHARS:
            dropped = len(text) - MAX_RESULT_CHARS
            text = text[:MAX_RESULT_CHARS] + f"\n[truncated: {dropped} more characters; narrow the query]"
        return text


REGISTRY: list[Tool] = []


def tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None,
         requires_env: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a function as an agent tool with a JSON-Schema input."""
    schema = {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY.append(Tool(name, description, schema, func, requires_env))
        return func

    return decorator


_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def _check(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected = schema.get("type")
    if expected:
        ok = isinstance(value, _TYPES[expected])
        if expected in ("integer", "number") and isinstance(value, bool):
            ok = False
        if not ok:
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: must be >= {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        errors.append(f"{path}: must be <= {schema['maximum']}")
    if expected == "string" and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: must not be empty")
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: at most {schema['maxItems']} items")
        for i, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{i}]", errors)


def validate(schema: dict[str, Any], data: Any) -> list[str]:
    """Check a tool input against its schema. Returns a list of problems (empty if valid).

    Tool inputs are streamed eagerly, so the API does not validate them for us:
    a truncated or malformed input must never reach the tool function.
    """
    if not isinstance(data, dict):
        return [f"input must be an object, got {type(data).__name__}"]
    errors: list[str] = []
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in data:
            errors.append(f"missing required field '{key}'")
    for key, value in data.items():
        if key not in props:
            errors.append(f"unknown field '{key}'")
        else:
            _check(value, props[key], key, errors)
    return errors

"""All tools the research agent can call. Importing a module registers its tools."""

from . import chembl, cheminformatics, literature, materials, pubchem, reports  # noqa: F401
from .base import REGISTRY, Tool, ToolContext, ToolError, validate


def available_tools() -> list[Tool]:
    """Tools whose requirements (e.g. an API key) are met, in a stable order for prompt caching."""
    return [t for t in REGISTRY if t.available]


__all__ = ["REGISTRY", "Tool", "ToolContext", "ToolError", "available_tools", "validate"]

"""Research-gap workflow: literature -> structured extraction -> gaps -> novelty check -> study designs."""

from .llm import LLM
from .pipeline import GapWorkflow, WorkflowConfig, default_run_dir

__all__ = ["LLM", "GapWorkflow", "WorkflowConfig", "default_run_dir"]

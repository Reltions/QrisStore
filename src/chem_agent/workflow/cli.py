"""`chem-gaps "topic"`: run the research-gap workflow from the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic

from ..agent import EFFORT_LEVELS, RefusalError
from .llm import LLM, IncompleteOutputError
from .pipeline import GapWorkflow, WorkflowConfig, default_run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chem-gaps",
        description="Find research gaps in a chemistry topic and design studies to fill them.",
    )
    parser.add_argument("topic", nargs="+", help="Research topic, e.g. 'stability of tin halide perovskites'.")
    parser.add_argument("-c", "--context", default="", help="Your goals or constraints (lab equipment, focus).")
    parser.add_argument("-y", "--year-from", type=int, help="Only search papers from this year on.")
    parser.add_argument("-n", "--max-papers", type=int, default=30, help="Papers to extract after screening (default 30).")
    parser.add_argument("--per-query", type=int, default=20, help="Results per search query (default 20).")
    parser.add_argument("--max-candidates", type=int, default=150, help="Papers to screen (default 150).")
    parser.add_argument("-g", "--max-gaps", type=int, default=6, help="Gaps to identify (default 6).")
    parser.add_argument("--no-full-text", action="store_true", help="Use abstracts only.")
    parser.add_argument("-e", "--effort", choices=EFFORT_LEVELS, default="high", help="Effort for gap analysis and study design.")
    parser.add_argument("--extract-effort", choices=EFFORT_LEVELS, default="medium", help="Effort for planning, screening, extraction.")
    parser.add_argument("-m", "--model", help="Claude model ID (default claude-opus-5).")
    parser.add_argument("-o", "--run-dir", help="Run folder (default runs/<date>-<topic>). Re-run with the same folder to resume.")
    parser.add_argument("-w", "--workers", type=int, default=4, help="Parallel Claude calls (default 4).")
    args = parser.parse_args(argv)

    topic = " ".join(args.topic)
    config = WorkflowConfig(
        topic=topic,
        run_dir=Path(args.run_dir) if args.run_dir else default_run_dir(topic),
        context=args.context,
        year_from=args.year_from,
        results_per_query=args.per_query,
        max_candidates=args.max_candidates,
        max_papers=args.max_papers,
        max_gaps=args.max_gaps,
        full_text=not args.no_full_text,
        effort=args.effort,
        extract_effort=args.extract_effort,
        workers=args.workers,
    )
    llm = LLM(model=args.model)
    print(f"Run folder: {config.run_dir}")
    try:
        GapWorkflow(config, llm=llm).run()
    except anthropic.AuthenticationError:
        print("Authentication failed. Set ANTHROPIC_API_KEY or run `ant auth login`.", file=sys.stderr)
        return 1
    except (anthropic.APIError, RuntimeError, RefusalError, IncompleteOutputError) as exc:
        print(f"Stopped: {exc}\nRe-run the same command to resume from the last completed step.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run the same command to resume.", file=sys.stderr)
        return 130
    finally:
        u = llm.usage
        print(f"Tokens: {u['input_tokens']:,} input, {u['output_tokens']:,} output")
    return 0


if __name__ == "__main__":
    sys.exit(main())

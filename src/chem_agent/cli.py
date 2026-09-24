"""Command-line interface: `chem-agent "question"` or `chem-agent` for an interactive session."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import anthropic

from .agent import EFFORT_LEVELS, AgentEvents, RefusalError, ResearchAgent
from .prompts import report_instruction

HELP = """Commands:
  /report [focus]  write a report of the research so far to the reports folder
  /reset           start a new conversation
  /usage           show token usage
  /quit            exit"""


class Console(AgentEvents):
    def __init__(self, color: bool) -> None:
        self.dim = "\033[2m" if color else ""
        self.cyan = "\033[36m" if color else ""
        self.red = "\033[31m" if color else ""
        self.reset = "\033[0m" if color else ""
        self.at_line_start = True

    def _line(self, text: str) -> None:
        if not self.at_line_start:
            print()
        print(text, flush=True)
        self.at_line_start = True

    def on_text(self, delta: str) -> None:
        print(delta, end="", flush=True)
        self.at_line_start = delta.endswith("\n")

    def on_thinking(self, delta: str) -> None:
        print(f"{self.dim}{delta}{self.reset}", end="", flush=True)
        self.at_line_start = delta.endswith("\n")

    def on_tool_call(self, name: str, args: dict[str, Any]) -> None:
        summary = json.dumps(args, ensure_ascii=False)
        if len(summary) > 140:
            summary = summary[:137] + "..."
        self._line(f"{self.cyan}> {name}{self.reset} {self.dim}{summary}{self.reset}")

    def on_tool_result(self, name: str, ok: bool, content: str) -> None:
        if not ok:
            self._line(f"{self.red}  ! {name}: {content[:200]}{self.reset}")

    def on_notice(self, message: str) -> None:
        self._line(f"{self.dim}[{message}]{self.reset}")


def _run(agent: ResearchAgent, console: Console, prompt: str) -> bool:
    try:
        agent.ask(prompt)
        console._line("")
        return True
    except RefusalError as exc:
        console.on_notice(str(exc))
    except anthropic.AuthenticationError:
        console.on_notice("Authentication failed. Set ANTHROPIC_API_KEY (see .env.example) or run `ant auth login`.")
    except anthropic.RateLimitError:
        console.on_notice("Rate limited by the API. Wait a minute and try again.")
    except anthropic.BadRequestError as exc:
        console.on_notice(f"Bad request: {exc.message}")
    except anthropic.APIStatusError as exc:
        console.on_notice(f"API error {exc.status_code}: {exc.message}")
    except anthropic.APIConnectionError:
        console.on_notice("Could not reach the Anthropic API. Check your internet connection.")
    return False


def _usage(agent: ResearchAgent) -> str:
    u = agent.usage
    return (f"tokens: {u['input_tokens']:,} input, {u['cache_read_input_tokens']:,} cached, "
            f"{u['cache_creation_input_tokens']:,} cache-write, {u['output_tokens']:,} output")


def interactive(agent: ResearchAgent, console: Console) -> None:
    print(f"Chemistry research agent ({agent.model}, effort {agent.effort}). Type /help for commands.\n")
    while True:
        try:
            line = input("chem> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return
        if line == "/help":
            print(HELP)
        elif line == "/reset":
            agent.reset()
            print("New conversation.")
        elif line == "/usage":
            print(_usage(agent))
        elif line.startswith("/report"):
            focus = line[len("/report"):].strip()
            prompt = "Write a complete research report of everything we have found so far"
            _run(agent, console, report_instruction(f"{prompt}{f', focused on: {focus}' if focus else ''}."))
        elif line.startswith("/"):
            print(f"Unknown command. {HELP}")
        else:
            try:
                _run(agent, console, line)
            except KeyboardInterrupt:
                console.on_notice("Interrupted; that question was discarded.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chem-agent", description="Chemistry research agent powered by Claude.")
    parser.add_argument("question", nargs="*", help="Research question. Omit for an interactive session.")
    parser.add_argument("-r", "--report", action="store_true", help="Save a Markdown report when done.")
    parser.add_argument("-e", "--effort", choices=EFFORT_LEVELS, default="high",
                        help="Reasoning effort: lower is faster and cheaper (default: high).")
    parser.add_argument("-m", "--model", help="Claude model ID (default: claude-opus-5, or $CHEM_AGENT_MODEL).")
    parser.add_argument("-o", "--output-dir", default="reports", help="Where reports and drawings go.")
    parser.add_argument("--no-web", action="store_true", help="Disable web search and web fetch.")
    parser.add_argument("--thinking", action="store_true", help="Show a summary of the model's reasoning.")
    parser.add_argument("--max-steps", type=int, default=40, help="Maximum model calls per question.")
    args = parser.parse_args(argv)

    console = Console(color=sys.stdout.isatty())
    agent = ResearchAgent(
        model=args.model,
        effort=args.effort,
        web=not args.no_web,
        output_dir=args.output_dir,
        max_steps=args.max_steps,
        show_thinking=args.thinking,
        events=console,
    )

    if not args.question:
        interactive(agent, console)
        return 0

    question = " ".join(args.question)
    ok = _run(agent, console, report_instruction(question) if args.report else question)
    console.on_notice(_usage(agent))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

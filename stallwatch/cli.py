"""Command-line interface: ``stallwatch check <trace-file>``."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .parsers import parse_trace_file
from .report import analyze, render_json, render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stallwatch",
        description="Detect stalls in a finished coding-agent run trace.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check",
        help="Analyse a trace file and report whether the agent stalled.",
    )
    check.add_argument("trace_file", help="Path to a JSONL trace file.")
    check.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Report format (default: text).",
    )
    check.add_argument(
        "--min-repeat",
        type=int,
        default=3,
        help="Repeat count that counts as a loop (default: 3).",
    )
    check.add_argument(
        "--window",
        type=int,
        default=8,
        help="Stagnation window: consecutive non-state-changing steps "
        "(default: 8).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_repeat < 2:
        parser.error("--min-repeat must be at least 2")
    if args.window < 2:
        parser.error("--window must be at least 2")
    try:
        parsed = parse_trace_file(args.trace_file)
    except FileNotFoundError:
        message = f"Trace file not found: {args.trace_file}"
        if args.format == "json":
            print(
                json.dumps(
                    {"verdict": "UNKNOWN", "exit_code": 3, "error": message},
                    indent=2,
                )
            )
        else:
            print(f"Verdict: UNKNOWN (exit 3)\nError: {message}", file=sys.stderr)
        return 3
    except OSError as exc:
        message = f"Could not read trace file: {exc}"
        if args.format == "json":
            print(
                json.dumps(
                    {"verdict": "UNKNOWN", "exit_code": 3, "error": message},
                    indent=2,
                )
            )
        else:
            print(f"Verdict: UNKNOWN (exit 3)\nError: {message}", file=sys.stderr)
        return 3
    analysis = analyze(
        parsed.steps,
        min_repeat=args.min_repeat,
        window=args.window,
        skipped_lines=parsed.skipped_lines,
    )
    if args.format == "json":
        print(render_json(analysis), end="")
    else:
        print(render_text(analysis), end="")
    return analysis.exit_code


def entry() -> None:
    sys.exit(main())

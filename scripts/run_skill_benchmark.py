#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Smith skill vs GitHub MCP benchmark.",
    )
    parser.add_argument("--model", required=True, help="Model identifier to use for both configurations.")
    parser.add_argument("--runs", type=int, default=3, help="Runs per configuration (default: 3).")
    parser.add_argument(
        "--eval-id",
        default="all",
        help="Eval id, comma-separated eval ids, or 'all' (default).",
    )
    parser.add_argument(
        "--workspace",
        help="Output workspace directory. Defaults to benchmarks/workspaces/<timestamp>.",
    )
    parser.add_argument(
        "--config",
        choices=["smith_skill", "github_mcp", "all"],
        default="all",
        help="Run only one configuration or both (default: all).",
    )
    parser.add_argument(
        "--executor",
        choices=["openai", "copilot"],
        default="openai",
        help="Benchmark executor backend (default: openai).",
    )
    return parser


async def _main() -> int:
    from smith.benchmark.runner import parse_eval_ids, run_benchmark

    parser = build_parser()
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve() if args.workspace else None
    selected_eval_ids = parse_eval_ids(args.eval_id)
    output_path = await run_benchmark(
        model=args.model,
        runs=max(1, int(args.runs)),
        eval_ids=selected_eval_ids,
        workspace=workspace,
        config=args.config,
        executor=args.executor,
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))

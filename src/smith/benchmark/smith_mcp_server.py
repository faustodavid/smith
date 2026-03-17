from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from smith.benchmark.constants import BENCHMARK_GITHUB_ORG_DISPLAY
from smith.benchmark.smith_cli import execute_smith_cli_command

mcp = FastMCP(
    name="smith-benchmark",
    instructions=f"Expose a single read-only Smith CLI tool scoped to the {BENCHMARK_GITHUB_ORG_DISPLAY} GitHub organization.",
    log_level="ERROR",
)


@mcp.tool()
def smith_cli(command: str) -> str:
    """Run a read-only Smith command against GitHub repositories in the OpenAI org."""

    return execute_smith_cli_command(command)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

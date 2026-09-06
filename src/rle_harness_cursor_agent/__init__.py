"""rle-harness-cursor-agent — Cursor Agent / CLI as an RLE harness.

Cursor Agent (https://cursor.com/docs/cli/overview) is Cursor's terminal
coding agent with a scriptable print mode (``agent -p``) and native MCP
support via project ``.cursor/mcp.json``. This package registers it with
RLE (https://github.com/AppSprout-dev/RLE) so

    python scripts/run_benchmark.py --harness cursor-agent --model composer-2

benchmarks *Cursor Agent as the harness* on the same scenarios, saves and
scoring as every other harness. Each tick is one headless invocation
(``agent -p ... --output-format json``) resuming the previous session; the
agent acts through the RLE MCP tools and calls ``end_turn``.
"""

from rle_harness_cursor_agent.plugin import PLUGIN, CursorAgentPlugin

__all__ = ["PLUGIN", "CursorAgentPlugin"]

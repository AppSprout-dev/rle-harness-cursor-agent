"""Entry point for ``--harness cursor-agent``."""

from __future__ import annotations

from pydantic import BaseModel
from rle.harness import Availability, BaseHarness, HarnessContext
from rle.testing.scripted_agent import ScriptedMcpHarness

from rle_harness_cursor_agent.harness import CursorAgentHarness, binary_version, resolve_binary
from rle_harness_cursor_agent.options import CursorAgentOptions


class CursorAgentPlugin:
    name = "cursor-agent"
    description = (
        "Cursor Agent / CLI coding agent (headless `agent -p`, session resumed each tick) "
        "acting through the RLE MCP tools."
    )

    def available(self) -> Availability:
        if resolve_binary("agent") is not None:
            return Availability.available()
        return Availability.missing(
            "Cursor Agent binary not on PATH (`agent` or `cursor-agent`). "
            "Install: curl https://cursor.com/install -fsS | bash "
            "or pass --harness-opt binary=/path/to/agent.",
        )

    def option_schema(self) -> type[BaseModel]:
        return CursorAgentOptions

    def create(self, ctx: HarnessContext, options: BaseModel) -> BaseHarness:
        assert isinstance(options, CursorAgentOptions)
        if resolve_binary(options.binary) is None:
            raise RuntimeError(
                f"Cursor Agent binary {options.binary!r} not found; set --harness-opt "
                "binary=/path/to/agent (or cursor-agent)",
            )
        return CursorAgentHarness(options)

    def smoke(self, ctx: HarnessContext, options: BaseModel) -> BaseHarness:
        """No Cursor Agent needed: a scripted agent plays the MCP round trip."""
        assert isinstance(options, CursorAgentOptions)
        return ScriptedMcpHarness(options, name=self.name)

    def describe(self) -> dict[str, str]:
        return {"harness": self.name, "cursor-agent": binary_version("agent")}


PLUGIN = CursorAgentPlugin()

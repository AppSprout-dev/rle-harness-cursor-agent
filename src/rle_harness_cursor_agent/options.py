"""Options for the Cursor Agent harness (``--harness-opt key=value``)."""

from __future__ import annotations

from pydantic import Field
from rle.harness.cli_base import HeadlessCliOptions


class CursorAgentOptions(HeadlessCliOptions):
    """CLI-agent knobs on top of :class:`HeadlessCliOptions`.

    Inherited (cli_base defaults): ``model``, ``turn_timeout_s`` (180),
    ``idle_grace_s``, ``extra_instructions``, ``mcp_container_reachable``,
    ``mcp_bind_host``, ``mcp_advertise_host``, ``mcp_port``,
    ``mcp_advertise_url``.
    """

    binary: str = Field(
        default="agent",
        description=(
            "Cursor Agent executable (name or path). The installer also "
            "symlinks `cursor-agent`; either name works."
        ),
    )
    resume_session: bool = Field(
        default=True,
        description="Resume the same headless chat every tick (`--resume <id>`).",
    )
    api_key_env: str = Field(
        default="CURSOR_API_KEY",
        description=(
            "Env var holding the Cursor API key for headless auth "
            "(or use `agent login`)."
        ),
    )
    extra_args: list[str] = Field(
        default_factory=list,
        description="Additional raw flags appended to every invocation.",
    )

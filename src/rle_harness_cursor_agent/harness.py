"""Cursor Agent driven one headless invocation per tick.

Surface used (https://cursor.com/docs/cli/reference/parameters,
https://cursor.com/docs/cli/mcp, https://cursor.com/docs/cli/reference/output-format):

* ``agent -p "<prompt>" --output-format json --force --trust --approve-mcps
  --workspace <workdir> [--model MODEL] [--resume <session_id>]``
  -> one JSON object: ``type=result``, ``result``, ``session_id``,
  ``is_error``, ``duration_ms``
* Project ``.cursor/mcp.json`` declaring the RLE MCP server (hosted
  in-process by RLE over streamable HTTP)::

      { "mcpServers": { "rle": { "url": "http://127.0.0.1:PORT/mcp" } } }

  Cursor CLI has no ``--mcp-url`` flag; project ``mcp.json`` + ``--workspace``
  is the supported attach path. ``--approve-mcps`` skips the approval prompt
  required for MCP tools in print mode.
* Auth is ``CURSOR_API_KEY`` (preferred for unattended runs) or a prior
  ``agent login``. The key is never written to disk by this harness.

Exit: 0 ok; non-zero writes an error to stderr and emits no JSON object.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from rle.harness import HarnessStepError
from rle.harness.cli_base import HeadlessCliHarness, TurnResult

from rle_harness_cursor_agent.options import CursorAgentOptions

logger = logging.getLogger(__name__)

__all__ = (
    "BINARY_CANDIDATES",
    "CursorAgentHarness",
    "DEFAULT_ADVERTISED_MCP_URL",
    "TOOL_NAMING_NOTE",
    "WORKSPACE_AGENTS_MD",
    "binary_version",
    "build_command",
    "mcp_config",
    "parse_json_output",
    "resolve_binary",
    "write_workspace_mcp_config",
)

BINARY_CANDIDATES = ("agent", "cursor-agent")
DEFAULT_ADVERTISED_MCP_URL = "http://host.docker.internal:8766/mcp"
MCP_SERVER_NAME = "rle"

TOOL_NAMING_NOTE = (
    "In this environment the colony is managed only through the `rle` MCP tools "
    "(get_brief, work_priority, blueprint, ..., end_turn — names may be prefixed "
    "with the server id). Call get_brief first, act only through those tools, "
    "and finish with end_turn. Do not search for other MCP servers."
)

WORKSPACE_AGENTS_MD = (
    "# RLE colony turn\n\n"
    "You manage a live RimWorld colony through the `rle` MCP tools only.\n"
    "Call `get_brief` first. Use MAP_SUMMARY coordinates verbatim.\n"
    "Act only through RLE tools (never shell into the game). Finish with `end_turn`.\n"
    "Doing nothing is allowed — still call `end_turn`.\n"
)


def resolve_binary(binary: str) -> str | None:
    """Resolve ``binary`` to an executable path.

    Accepts an absolute/relative path, a PATH name, or the official installer
    names ``agent`` / ``cursor-agent`` (tried as fallbacks for each other).
    """
    path = Path(binary).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return str(path.resolve())
    found = shutil.which(binary)
    if found:
        return found
    if Path(binary).name in BINARY_CANDIDATES:
        for name in BINARY_CANDIDATES:
            found = shutil.which(name)
            if found:
                return found
    return None


def mcp_config(url: str) -> dict[str, Any]:
    """Project ``.cursor/mcp.json``: RLE as a remote streamable-HTTP server."""
    return {"mcpServers": {MCP_SERVER_NAME: {"url": url}}}


def write_workspace_mcp_config(workdir: Path, url: str) -> Path:
    """Write ``.cursor/mcp.json`` (and a short ``AGENTS.md``) into *workdir*."""
    cursor_dir = workdir / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    dest = cursor_dir / "mcp.json"
    dest.write_text(json.dumps(mcp_config(url), indent=2) + "\n", encoding="utf-8")
    (workdir / "AGENTS.md").write_text(WORKSPACE_AGENTS_MD, encoding="utf-8")
    return dest


def build_command(
    binary: str,
    prompt: str,
    opts: CursorAgentOptions,
    *,
    model: str | None,
    workspace: str,
    session_id: str | None,
) -> list[str]:
    """Headless print-mode argv. Cursor has no ``--mcp-url``; MCP is the workspace file."""
    cmd = [
        binary,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--force",
        "--trust",
        "--approve-mcps",
        "--workspace",
        workspace,
    ]
    if model:
        cmd += ["--model", model]
    if session_id and opts.resume_session:
        cmd += ["--resume", session_id]
    cmd += list(opts.extra_args)
    return cmd


def _usage_int(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in usage and usage[key] is not None:
            return int(usage[key] or 0)
    return 0


def parse_json_output(stdout: str) -> TurnResult:
    """Turn the print-mode ``json`` object into a TurnResult (tolerant of noise)."""
    data: Any = None
    text = stdout.strip()
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return TurnResult(text=text)
    if not isinstance(data, dict):
        return TurnResult(text=text)
    if data.get("is_error") or data.get("subtype") == "error" or data.get("type") == "error":
        raise HarnessStepError(f"cursor-agent reported an error: {data.get('result', data)}")
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt_tokens = _usage_int(usage, "input_tokens", "inputTokens", "prompt_tokens")
    cached = _usage_int(usage, "cache_read_tokens", "cacheReadTokens", "cache_read_input_tokens")
    cached += _usage_int(
        usage, "cache_write_tokens", "cacheWriteTokens", "cache_creation_input_tokens",
    )
    return TurnResult(
        text=str(data.get("result", data.get("text", ""))),
        prompt_tokens=prompt_tokens + cached,
        completion_tokens=_usage_int(usage, "output_tokens", "outputTokens", "completion_tokens"),
        reasoning_tokens=_usage_int(usage, "reasoning_tokens", "reasoningTokens"),
        extras={
            "session_id": str(data.get("session_id", data.get("sessionId", ""))),
            "request_id": data.get("request_id"),
            "duration_ms": data.get("duration_ms"),
            "subtype": data.get("subtype"),
        },
    )


class CursorAgentHarness(HeadlessCliHarness):
    name: ClassVar[str] = "cursor-agent"

    def __init__(self, options: CursorAgentOptions) -> None:
        super().__init__(options)
        self.opts = options
        self._binary: str | None = None
        self._workdir: str | None = None
        self._session_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._mcp_url: str | None = None

    def render_prompt(self, brief: Any) -> str:
        return super().render_prompt(brief) + "\n\n" + TOOL_NAMING_NOTE

    async def start_agent(self, mcp_url: str) -> None:
        binary = resolve_binary(self.opts.binary)
        if binary is None:
            raise HarnessStepError(
                f"Cursor Agent binary {self.opts.binary!r} not found on PATH "
                "(install: curl https://cursor.com/install -fsS | bash)",
            )
        self._binary = binary
        self._workdir = tempfile.mkdtemp(prefix="rle-cursor-agent-")
        self._mcp_url = mcp_url
        write_workspace_mcp_config(Path(self._workdir), mcp_url)
        logger.info(
            "Cursor Agent workspace=%s with RLE MCP at %s",
            self._workdir, mcp_url,
        )
        if not os.environ.get(self.opts.api_key_env):
            logger.info(
                "%s not set --- relying on `agent login` cached credentials",
                self.opts.api_key_env,
            )

    async def send_turn(self, prompt: str) -> TurnResult:
        assert self._binary is not None and self._workdir is not None
        cmd = build_command(
            self._binary, prompt, self.opts,
            model=self.opts.model or self.ctx.config.model,
            workspace=self._workdir, session_id=self._session_id,
        )
        logger.debug(
            "cursor-agent invocation: %s",
            " ".join(cmd[:1] + ["-p", "<prompt>"] + cmd[3:]),
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=self._workdir,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        self._proc = proc
        try:
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            await self._terminate_process(proc)
            raise
        finally:
            if self._proc is proc:
                self._proc = None
        returncode = proc.returncode or 0
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if stderr.strip():
            logger.debug("cursor-agent stderr (tail): %s", stderr.strip()[-1500:])
        if returncode != 0:
            raise HarnessStepError(
                f"cursor-agent exited {returncode}: {(stderr or stdout).strip()[-800:]}",
            )
        turn = parse_json_output(stdout)
        sid = turn.extras.get("session_id")
        if sid:
            self._session_id = str(sid)
        return turn

    async def _terminate_process(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass

    async def abort_turn(self) -> None:
        proc = self._proc
        if proc is not None:
            await self._terminate_process(proc)
        if self._proc is proc:
            self._proc = None

    async def stop_agent(self) -> None:
        await self.abort_turn()
        if self._workdir is not None:
            shutil.rmtree(self._workdir, ignore_errors=True)
            self._workdir = None

    def agent_versions(self) -> dict[str, str]:
        return {"cursor-agent": binary_version(self.opts.binary)}


def binary_version(binary: str) -> str:
    path = resolve_binary(binary)
    if path is None:
        return "not installed"
    try:
        out = subprocess.run(
            [path, "--version"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else "unknown"

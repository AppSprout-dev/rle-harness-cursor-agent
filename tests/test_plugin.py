"""Contract + invocation-shaping tests (no Cursor Agent binary required)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from rle.config import RLEConfig
from rle.harness import (
    HarnessContext,
    HarnessOptionsError,
    HarnessStepError,
    get_plugin,
    harness_names,
)
from rle.rimapi.client import RimAPIClient
from rle.testing import MockRimAPI, run_harness_smoke

from rle_harness_cursor_agent.harness import (
    DEFAULT_ADVERTISED_MCP_URL,
    CursorAgentHarness,
    binary_version,
    build_command,
    mcp_config,
    parse_json_output,
    write_workspace_mcp_config,
)
from rle_harness_cursor_agent.options import CursorAgentOptions

NAME = "cursor-agent"


class TestRegistration:
    def test_entry_point(self) -> None:
        assert NAME in harness_names()
        assert get_plugin(NAME).option_schema() is CursorAgentOptions

    async def test_smoke_round_trip(self) -> None:
        report = await run_harness_smoke(NAME, ticks=2)
        assert report.ok and report.harness == NAME
        assert all(t.execution.executed == 1 for t in report.ticks)
        assert all(t.extras["turn_ended"] for t in report.ticks)

    async def test_options_validated(self) -> None:
        with pytest.raises(HarnessOptionsError):
            await run_harness_smoke(NAME, ticks=1, options={"not_a_real_option": True})


class TestInvocationShaping:
    def test_mcp_config(self) -> None:
        cfg = mcp_config("http://127.0.0.1:7000/mcp")
        assert cfg["mcpServers"]["rle"] == {"url": "http://127.0.0.1:7000/mcp"}

    def test_write_workspace_mcp_config(self, tmp_path: Path) -> None:
        dest = write_workspace_mcp_config(tmp_path, "http://127.0.0.1:9/mcp")
        assert dest == tmp_path / ".cursor" / "mcp.json"
        written = json.loads(dest.read_text(encoding="utf-8"))
        assert written["mcpServers"]["rle"]["url"] == "http://127.0.0.1:9/mcp"
        assert (tmp_path / "AGENTS.md").is_file()

    def test_build_command_first_tick(self) -> None:
        cmd = build_command(
            "/bin/agent", "do it", CursorAgentOptions(),
            model="composer-2", workspace="/tmp/w", session_id=None,
        )
        assert cmd[:3] == ["/bin/agent", "-p", "do it"]
        assert cmd[cmd.index("--output-format") + 1] == "json"
        assert "--force" in cmd and "--trust" in cmd and "--approve-mcps" in cmd
        assert cmd[cmd.index("--workspace") + 1] == "/tmp/w"
        assert cmd[cmd.index("--model") + 1] == "composer-2"
        assert "--resume" not in cmd

    def test_build_command_resumes(self) -> None:
        cmd = build_command(
            "agent", "again", CursorAgentOptions(),
            model=None, workspace="/tmp/w", session_id="chat-abc",
        )
        assert cmd[cmd.index("--resume") + 1] == "chat-abc"
        assert "--model" not in cmd
        cmd = build_command(
            "agent", "again", CursorAgentOptions(resume_session=False),
            model=None, workspace="/tmp/w", session_id="chat-abc",
        )
        assert "--resume" not in cmd

    def test_build_command_extra_args(self) -> None:
        cmd = build_command(
            "agent", "x", CursorAgentOptions(extra_args=["--sandbox", "disabled"]),
            model=None, workspace="/tmp/w", session_id=None,
        )
        assert cmd[-2:] == ["--sandbox", "disabled"]

    def test_parse_json_output(self) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "Placed walls.",
            "session_id": "s-1",
            "request_id": "req-9",
            "duration_ms": 1234,
            "usage": {"inputTokens": 700, "cacheReadTokens": 4000, "outputTokens": 180},
        }
        turn = parse_json_output("some log line\n" + json.dumps(payload))
        assert turn.text == "Placed walls."
        assert (turn.prompt_tokens, turn.completion_tokens) == (4700, 180)
        assert turn.extras["session_id"] == "s-1"
        assert turn.extras["request_id"] == "req-9"
        assert turn.extras["duration_ms"] == 1234

    def test_parse_error_object(self) -> None:
        with pytest.raises(HarnessStepError, match="auth failed"):
            parse_json_output('{"type":"result","is_error":true,"result":"auth failed"}')

    def test_parse_non_json(self) -> None:
        assert parse_json_output("plain text").text == "plain text"


def _fake_agent(tmp_path: Path) -> Path:
    """A stand-in `agent` that records argv and emits a print-mode json object."""
    script = tmp_path / "agent"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        f"open({str(tmp_path / 'argv.json')!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "assert os.path.exists(os.path.join(os.getcwd(), '.cursor', 'mcp.json'))\n"
        "print(json.dumps({\n"
        "    'type': 'result', 'subtype': 'success', 'is_error': False,\n"
        "    'result': 'ok', 'session_id': 'sess-9',\n"
        "    'usage': {'inputTokens': 5, 'outputTokens': 1},\n"
        "}))\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _prompt_argvs(tmp_path: Path) -> list[list[str]]:
    lines = (tmp_path / "argv.json").read_text().splitlines()
    return [json.loads(line) for line in lines]


class TestAgainstFakeBinary:
    async def test_two_turns_resume_session(self, tmp_path: Path) -> None:
        fake = _fake_agent(tmp_path)
        harness = CursorAgentHarness(CursorAgentOptions(binary=str(fake), model="composer-2"))
        mock = MockRimAPI()
        async with RimAPIClient("http://mock") as client:
            mock.attach(client)
            harness._ctx = HarnessContext(config=RLEConfig(tick_interval=0.0), client=client)
            await harness.start_agent("http://127.0.0.1:1/mcp")
            try:
                turn1 = await harness.send_turn("turn one")
                turn2 = await harness.send_turn("turn two")
            finally:
                await harness.stop_agent()
        assert turn1.text == "ok" and turn1.prompt_tokens == 5
        assert turn2.extras["session_id"] == "sess-9"
        argvs = _prompt_argvs(tmp_path)
        assert len(argvs) == 2
        assert "--resume" not in argvs[0]
        assert argvs[1][argvs[1].index("--resume") + 1] == "sess-9"
        assert argvs[0][argvs[0].index("--model") + 1] == "composer-2"
        assert "--approve-mcps" in argvs[0] and "--force" in argvs[0] and "--trust" in argvs[0]

    async def test_advertise_url_written_to_workspace(self, tmp_path: Path) -> None:
        fake = _fake_agent(tmp_path)
        harness = CursorAgentHarness(CursorAgentOptions(
            binary=str(fake),
            mcp_advertise_url=DEFAULT_ADVERTISED_MCP_URL,
        ))
        mock = MockRimAPI()
        async with RimAPIClient("http://mock") as client:
            mock.attach(client)
            harness._ctx = HarnessContext(config=RLEConfig(tick_interval=0.0), client=client)
            # setup() already substitutes mcp_advertise_url; start_agent sees the advertised URL.
            await harness.start_agent(DEFAULT_ADVERTISED_MCP_URL)
            try:
                assert harness._workdir is not None
                cfg = json.loads(
                    (Path(harness._workdir) / ".cursor" / "mcp.json").read_text(encoding="utf-8"),
                )
                assert cfg["mcpServers"]["rle"]["url"] == DEFAULT_ADVERTISED_MCP_URL
            finally:
                await harness.stop_agent()

    async def test_nonzero_exit_is_a_step_error(self, tmp_path: Path) -> None:
        fake = tmp_path / "agent"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('auth failed', file=sys.stderr)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        harness = CursorAgentHarness(CursorAgentOptions(binary=str(fake)))
        mock = MockRimAPI()
        async with RimAPIClient("http://mock") as client:
            mock.attach(client)
            harness._ctx = HarnessContext(config=RLEConfig(tick_interval=0.0), client=client)
            await harness.start_agent("http://127.0.0.1:1/mcp")
            try:
                with pytest.raises(HarnessStepError, match="auth failed"):
                    await harness.send_turn("x")
            finally:
                await harness.stop_agent()

    def test_binary_version_missing(self) -> None:
        assert binary_version("/definitely/not/installed-cursor-agent") == "not installed"

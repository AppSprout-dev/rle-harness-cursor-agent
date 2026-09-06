# rle-harness-cursor-agent

[Cursor Agent / CLI](https://cursor.com/docs/cli/overview) as an [RLE](https://github.com/AppSprout-dev/RLE) harness.

RLE benchmarks **harnesses × models** on a live RimWorld colony. This package lets
Cursor's headless coding agent be the harness: each tick is one print-mode
invocation (`agent -p … --output-format json`) that resumes the previous
session, acts on the colony through the RLE MCP tools (`get_brief`,
`work_priority`, … `end_turn`), and the writes that reached the game are scored
with the same composite as every other harness (RLE `SCORING_VERSION` **1.2**).

## Install

```bash
# Cursor Agent / CLI (symlinks `agent` and `cursor-agent` into ~/.local/bin)
curl https://cursor.com/install -fsS | bash
# add ~/.local/bin to PATH if the installer says so
agent --version

# Auth: browser login, or an API key for unattended / CI runs
agent login
#   or:  export CURSOR_API_KEY=...     # never commit this

# RLE core (not on PyPI yet) + this harness
uv pip install "rimworld-learning-environment[mcp] @ git+https://github.com/AppSprout-dev/RLE"
uv pip install git+https://github.com/AppSprout-dev/rle-harness-cursor-agent
```

Do not put API keys in the repo, `mcp.json`, or harness options.

## Crashlanded (seed 42, scoring 1.2, 300s turns)

From an RLE checkout with RimWorld + RIMAPI running:

```bash
python scripts/run_benchmark.py --harness list
python scripts/run_scenario.py crashlanded \
  --harness cursor-agent \
  --seed 42 \
  --harness-opt turn_timeout_s=300
```

Scoring 1.2 is RLE's current composite (`SCORING_VERSION = "1.2"` in RLE core);
this harness does not override weights. `--seed 42` seeds RLE-side
stochasticity only (resolver tiebreaks) — it does not control RimWorld's RNG.

A few more ticks / a named model:

```bash
python scripts/run_scenario.py crashlanded \
  --harness cursor-agent \
  --model composer-2 \
  --ticks 10 --tick-interval 30 \
  --seed 42 \
  --harness-opt turn_timeout_s=300
```

## Options (`--harness-opt key=value`)

Inherited from `rle.harness.cli_base.HeadlessCliOptions` plus Cursor-specific
fields:

| Option | Default | Meaning |
|---|---|---|
| `binary` | `agent` | Executable name/path (`cursor-agent` also works) |
| `model` | RLE `--model` | Passed as `--model` |
| `turn_timeout_s` | 180 | Kill the invocation after this many seconds (use **300** for Crashlanded) |
| `idle_grace_s` | 3 | Wait for late tool calls if `end_turn` was not called |
| `extra_instructions` | – | Appended to every turn prompt |
| `resume_session` | true | `--resume <session_id>` every tick so context carries over |
| `extra_args` | – | Raw flags appended to every invocation |
| `mcp_advertise_url` | – | Full MCP URL written into workspace `mcp.json` instead of the bind URL |
| `mcp_container_reachable` | RLE config | Bind `0.0.0.0` and advertise `http://host.docker.internal:<port>/mcp` |
| `mcp_bind_host` / `mcp_advertise_host` / `mcp_port` | RLE config | Listen / advertise overrides |

## How MCP is attached (no `--mcp-url` flag)

Cursor CLI does **not** take an MCP URL on the command line. The supported
headless path this harness uses:

1. RLE hosts the MCP server in-process (streamable HTTP).
2. The harness writes a temp workspace with `.cursor/mcp.json`:

   ```json
   { "mcpServers": { "rle": { "url": "http://127.0.0.1:<port>/mcp" } } }
   ```

3. Each tick:

   ```text
   agent -p "<prompt>" --output-format json --force --trust --approve-mcps
         --workspace <tempdir> [--model <id>] [--resume <session_id>]
   ```

`--approve-mcps` is required so print mode (`-p`) actually injects MCP tools
instead of prompting. `--force` / `--trust` keep the run non-interactive.
`--output-format json` yields `{type, result, session_id, …}` ([docs](https://cursor.com/docs/cli/reference/output-format)).

**Caveats**

- Cursor also reads `~/.cursor/mcp.json`. Extra global servers may appear
  alongside `rle`. For a clean run, keep the global file empty or RLE-only.
- Some CLI builds historically listed MCP tools via `agent mcp list-tools`
  but did not inject them in `-p` without `--approve-mcps`. If the agent
  never calls `get_brief`, confirm the CLI version (`agent --version`) and
  that `--approve-mcps` is on the argv (it is, unless you override
  `extra_args` in a way that breaks the invocation).
- Token counts are **not** in the documented JSON result object; RLE's cost
  tracker stays at zero unless a future CLI adds a `usage` field (the parser
  already accepts one).

## Docker / container reachability

This harness runs the **host** `agent` binary. If the agent itself is inside
Docker while RimWorld/RLE stay on the host, the in-process MCP bind
(`127.0.0.1` + ephemeral port) is unreachable from the container. Pass:

```bash
python scripts/run_scenario.py crashlanded --harness cursor-agent \
  --harness-opt mcp_container_reachable=true \
  --harness-opt mcp_advertise_url=http://host.docker.internal:8766/mcp \
  --harness-opt turn_timeout_s=300 \
  --seed 42
```

That binds MCP on `0.0.0.0:8766` and writes the advertised URL into
`.cursor/mcp.json`. It does not change `--docker` (RIMAPI-in-container).

## How it works

- Temp workspace + `.cursor/mcp.json` pointing at RLE's in-process MCP host.
- Per tick: `agent -p` as above; `session_id` from the JSON object is reused
  via `--resume` so context carries over.
- `--smoke-test` needs no Cursor Agent: a scripted agent plays the same MCP
  round trip.

## Development

```bash
uv pip install "rimworld-learning-environment[mcp] @ git+https://github.com/AppSprout-dev/RLE"
uv pip install -e ".[dev]"
pytest && ruff check src tests && mypy src
```

MIT (this package). Cursor Agent is a Cursor product; install and auth terms
are Cursor's.

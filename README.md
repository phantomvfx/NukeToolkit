# Nuke

Nuke tools, AI integrations and a local knowledge base in one repo.

| Path | What |
|---|---|
| [`gizmos/`](gizmos/README.md) | Node tools saved as `.nk` Group snippets (AOV rebuilds, CornerPin helpers, DefocusTracker, CopyCat setup …) |
| [`python/`](python/README.md) | Python tools and the **mllamas tools** menu (tool paste, Send to RV) |
| [`tools/comfy/`](tools/comfy/README.md) | **NukeComfy**: Nuke ↔ ComfyUI bridge (KleinEdit, DepthAny, NormalCrafter, QwenInpaint, Flux3Edit) |
| [`tools/mcp/`](tools/mcp/README.md) | **NukeMCP**: MCP server so Claude can drive a live Nuke session |
| [`tools/taptrack/`](tools/taptrack/README.md) | **TAPTrack**: DeepMind TAPNext/TAPIR point tracking exported to Tracker4, CornerPin2D or Transform |
| [`knowledge/`](knowledge/README.md) | Converted Nuke 13/16/17 docs + tutorials (local only, gitignored) |
| [`docs/`](docs/) | [Setup](docs/setup.md) · [Architecture](docs/architecture.md) · [History](docs/history.md) |

## Quick start

```python
# ~/.nuke/init.py
nuke.pluginAddPath('L:/Nuke')
```

Restart Nuke to get the **NukeComfy** and **mllamas tools** menus, the
**TAPTrack** node, and the MCP listener on port 54321. Per-tool setup (venvs,
weights, ComfyUI models) is in [docs/setup.md](docs/setup.md).

Working with Claude Code: see [`CLAUDE.md`](CLAUDE.md) and the agents and
commands in [`.claude/`](.claude/).

Previously three repos: `phantomvfx/NukeDevSandbox`, `phantomvfx/NukeComfy` and `phantomvfx/NukeMCP`.

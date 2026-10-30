# Architecture

How the parts of the repo fit together at runtime.

```
                       ┌──────────────────────── Nuke (GUI or -t) ────────────────────────┐
~/.nuke/init.py ──────▶│ L:/Nuke/init.py  ──pluginAddPath──▶ python/  tools/comfy  tools/mcp  │
 pluginAddPath(L:/Nuke)│                                     tools/taptrack/nuke           │
                       │                                                                   │
                       │  mllamas tools menu ── nodePaste ──▶ gizmos/*.nk                  │
                       │  NukeComfy menu ───── nodePaste ──▶ tools/comfy/gizmos/*.nk       │
                       │     Generate ─▶ NukeComfy.python.* ──HTTP──▶ ComfyUI :8188        │
                       │  TAPTrack node ── job.json ─▶ worker subprocess (own venv, torch) │
                       │  nuke_mcp_listener ◀──TCP :54321── nuke_mcp_server ◀─stdio─ Claude │
                       └───────────────────────────────────────────────────────────────────┘
```

## Pieces

**Sandbox tools (`gizmos/`, `python/`).** Plain `.nk` Group snippets and
Python scripts. `python/mllamas_tools` builds the "mllamas tools" menu, which
pastes `.nk` files from `gizmos/` and sends Reads to RV. Build scripts
(`build_*.py`) generate tools headlessly, and `smoke_*.py` validate them under
`Nuke -t`.

**NukeComfy (`tools/comfy`).** Each tool's Generate button renders the inputs
to PNG or EXR, uploads them to ComfyUI, patches an API-format workflow from
`json/` by `NUKE_*` title markers, queues it, and polls on a background thread
(`polling.py`). The result is pulled back into the Group on the main thread.
The package name `NukeComfy` is frozen: root `init.py` aliases `tools/comfy` to it.

**NukeMCP (`tools/mcp`).** The MCP server (outside Nuke, Python 3.10 venv)
turns each MCP tool call into a Python snippet sent over TCP. The listener
inside Nuke `exec`s the snippet and returns `result`. This is how Claude
inspects or edits a live script and gets rendered previews back as images.

**TAPTrack (`tools/taptrack`).** The Nuke side (stdlib only) collects query
points and writes `job.json`. A separate worker process with torch and tapnet
runs the tracker and streams JSON progress lines. On Windows it's launched
through WMI, because torch can't initialise as a direct child of Nuke. The
results are exported as native Tracker4, CornerPin2D or Transform nodes that
work without the tool installed. Its `CLAUDE.md` has the hard-won details.

**Knowledge base (`knowledge/`).** Official docs converted to Markdown for
Nuke 13, 16 and 17, plus curated tutorials. Each version has a
`DOCS_INDEX.md` entry point. It's used by Claude (the `nuke-docs` agent) when
building tools against a specific Nuke version.

## Claude Code integration (`.claude/`)

| Item | Purpose |
|---|---|
| `agents/nuke-docs.md` | Version-scoped doc research in `knowledge/`. Returns cited answers without flooding the main context |
| `agents/nuke-tool-reviewer.md` | Reviews a `.nk` or Nuke Python tool against the repo conventions |
| `commands/new-tool.md` | `/new-tool`: scaffold a new `.nk` or Python tool with the standard header |
| `commands/smoke-test.md` | `/smoke-test`: run a script or tool headless in `Nuke -t` |
| `commands/nuke-status.md` | `/nuke-status`: check the live-Nuke MCP bridge end to end |
